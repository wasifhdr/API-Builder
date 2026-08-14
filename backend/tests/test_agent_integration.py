"""Opt-in end-to-end run against the real waltonbd.com: real plan, real
drive loop (no LLM mocking — this is the point of this test), real distill/
extract/verify. Hits the live network and spends real LLM API calls, so it
is skipped unless explicitly requested.

Run once per AGENT_MODEL candidate (see docs/superpowers/plans/2026-08-13-
autonomous-api-authoring.md's "Verified AGENT_MODEL candidates" table) by
setting AGENT_MODEL in .env before running — this file does not parametrize
across models itself, so one invocation is one live run against one model.

    RUN_AGENT_INTEGRATION=1 uv run pytest tests/test_agent_integration.py -v -s
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AGENT_INTEGRATION") != "1",
    reason="hits the live network and a real LLM; set RUN_AGENT_INTEGRATION=1 to run",
)


@pytest_asyncio.fixture(autouse=True)
async def _agent_integration_uses_test_db(engine, monkeypatch):
    import app.agent.runner as runner_module
    import app.recorder.session as recorder_session

    test_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(recorder_session, "async_session", test_sessionmaker)
    monkeypatch.setattr(runner_module, "async_session", test_sessionmaker)


@pytest.mark.asyncio
async def test_authors_a_walton_product_search_api(db, make_user, redis):
    """End-to-end against waltonbd.com: plan, drive, distill, extract, verify.

    Asserts the run reaches a passing verify — including the differential
    check, which is what proves the parameter is real rather than baked in —
    and reports attempts/tokens/wall-clock for calibrating agent_run_price_bdt.
    """
    import time

    from app.agent.runner import cmd_channel, run_agent
    from app.models.agent_run import AgentRunStatus
    from app.models.billing import PlanTier, Subscription, SubscriptionStatus
    from app.models.workflow import Workflow
    from app.redis import redis_client
    from app.services import agent_runs, plans, wallet
    from app.services.plans import invalidate_cache

    invalidate_cache()
    user = await make_user()
    db.add(Subscription(
        user_id=user.id, tier=PlanTier.MAX, status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await wallet.credit(user.id, Decimal("500.00"), "recharge", db)
    await db.commit()

    run = await agent_runs.create_run(
        user, "make me an API to search for products on the Walton website", db,
    )
    await db.commit()

    async def _confirm_repeatedly():
        message = json.dumps({"t": "confirm_url", "ok": True})
        for _ in range(60):
            await redis_client.publish(cmd_channel(run.id), message)
            await asyncio.sleep(0.5)

    started = time.monotonic()
    confirm_task = asyncio.create_task(_confirm_repeatedly())
    await run_agent(run.id)
    confirm_task.cancel()
    wall_clock = time.monotonic() - started

    await db.refresh(run)
    print(
        f"\nstatus={run.status.value} attempts={run.attempt} "
        f"tokens={run.token_usage} wall_clock={wall_clock:.1f}s "
        f"reason={run.failure_reason!r}"
    )
    assert run.status == AgentRunStatus.SUCCEEDED, run.failure_reason
    assert run.workflow_id is not None

    workflow = await db.get(Workflow, run.workflow_id)
    assert workflow.parameters, "no parameter was bound"
    assert workflow.extraction.get("main"), "no extraction config was built"

    balance, _ = await wallet.balances(user.id, db)
    price = await plans.agent_run_price(PlanTier.MAX, db)
    assert balance == Decimal("500.00") - price  # charge kept on success
