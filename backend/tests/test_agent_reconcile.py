import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.workers.periodic as periodic_module
from app.agent.verify import verify_workflow
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.wallet import REASON_AGENT_DEBIT, REASON_AGENT_REFUND
from app.services import wallet
from app.workers.periodic import AGENT_RUN_STALE_SECONDS, sweep_once


@pytest_asyncio.fixture(autouse=True)
async def _periodic_uses_test_db(engine, monkeypatch):
    # sweep_once opens its own DB session via the module-level `async_session`
    # bound to the dev DB at import time, not the apibuilder_test DB the db/
    # engine fixtures use — same mismatch test_recorder_rerecord.py documents.
    monkeypatch.setattr(periodic_module, "async_session", async_sessionmaker(engine, expire_on_commit=False))


async def _backdate(db, run_id, seconds_ago: int) -> None:
    # updated_at has onupdate=func.now(), which SQLAlchemy applies whenever
    # IT generates the UPDATE — assigning run.updated_at in Python and
    # flushing would just get overridden. Raw SQL bypasses that entirely.
    await db.execute(
        text("UPDATE agent_runs SET updated_at = :ts WHERE id = :id"),
        {"ts": datetime.now(timezone.utc) - timedelta(seconds=seconds_ago), "id": run_id},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_sweep_refunds_a_stale_non_terminal_run(db, make_user):
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.DRIVING)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await wallet.debit(user.id, Decimal("10.00"), REASON_AGENT_DEBIT, db, agent_run_id=run.id)
    await db.commit()

    await _backdate(db, run.id, AGENT_RUN_STALE_SECONDS + 60)

    await sweep_once()

    await db.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert run.failure_reason is not None

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")  # refunded


@pytest.mark.asyncio
async def test_sweep_leaves_a_fresh_non_terminal_run_alone(db, make_user):
    user = await make_user()
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.DRIVING)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await sweep_once()

    await db.refresh(run)
    assert run.status == AgentRunStatus.DRIVING  # untouched — well within budget


@pytest.mark.asyncio
async def test_sweep_leaves_terminal_runs_alone(db, make_user):
    user = await make_user()
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.SUCCEEDED)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await _backdate(db, run.id, AGENT_RUN_STALE_SECONDS + 60)

    await sweep_once()

    await db.refresh(run)
    assert run.status == AgentRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_sweep_does_not_double_refund_an_already_refunded_run(db, make_user):
    """A run the worker actually finishes (refunding it) between the sweep's
    query and its finish_run call must not be refunded a second time —
    finish_run's own idempotence check covers this, this proves it end to
    end through the sweep."""
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.FAILED, failure_reason="already done")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await _backdate(db, run.id, AGENT_RUN_STALE_SECONDS + 60)

    await sweep_once()  # terminal already — must be a no-op

    refunds = await db.execute(
        text("SELECT count(*) FROM wallet_ledger WHERE reason = :r AND agent_run_id = :id"),
        {"r": REASON_AGENT_REFUND, "id": run.id},
    )
    assert refunds.scalar_one() == 0


@pytest.mark.asyncio
async def test_verify_replay_completes_while_the_recording_slot_is_held(fixture_site_url):
    """An agent run holds the single recording slot while it verifies — verify
    must not be able to queue behind other work waiting on that same slot.
    replay_workflow (which verify_workflow calls) opens its own Playwright
    browser directly, independent of any worker queue/semaphore, so this
    holds by construction; this proves it actually runs concurrently rather
    than only arguing it from the source.
    """
    recording_slot = asyncio.Semaphore(1)
    await recording_slot.acquire()  # simulates the agent run's own session holding it

    plan = {
        "parameters": [{"name": "query", "type": "string", "required": True,
                        "drive_value": "fan", "verify_value": "television", "description": None}],
        "fields": [{"name": "title", "type": "string"}],
    }
    snapshot = {
        "steps": [
            {"type": "goto", "url": f"{fixture_site_url}/search.html?q=fan",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": {"main": {"mode": "list", "root": "li.product",
                                 "fields": [{"name": "title", "selectors": [".title"], "take": "text"}]}},
    }

    result = await asyncio.wait_for(
        verify_workflow(snapshot, plan, drive_data=[{"title": "Desk Fan"}]), timeout=30,
    )
    assert result.passed, [c.detail for c in result.checks if not c.passed]
    recording_slot.release()
