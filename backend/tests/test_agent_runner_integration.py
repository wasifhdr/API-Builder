"""End-to-end run_agent against the local fixture site: real Playwright, real
RecordingSession, real distill/extract/verify — only the LLM calls (build_plan,
complete_tools) are mocked, with the tool-calling mock computing real refs from
the actual observation text rather than hardcoding ref numbers, so it stays
correct if observe.js's traversal order ever changes."""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.agent.runner as runner_module
import app.recorder.session as recorder_session
from app.agent.runner import cmd_channel, run_agent
from app.llm.client import ToolCall, TurnResult
from app.models.agent_run import AgentRunStatus
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.workflow import Workflow, WorkflowStatus
from app.services import agent_runs, plans, wallet
from app.services.plans import invalidate_cache

_REF_RE = re.compile(r"\[(ref_\d+)\]")


@pytest_asyncio.fixture(autouse=True)
def _reset_plan_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest_asyncio.fixture(autouse=True)
async def _agent_uses_test_db(engine, redis, monkeypatch):
    # Both RecordingSession and app.agent.runner open their own DB sessions via
    # the module-level `async_session` bound to the dev DB at import time —
    # same mismatch test_recorder_rerecord.py documents and works around.
    test_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(recorder_session, "async_session", test_sessionmaker)
    monkeypatch.setattr(runner_module, "async_session", test_sessionmaker)
    # Same class of mismatch for Redis: RecordingSession.__init__ and every
    # app.agent.runner function capture the module-level `redis_client` (dev
    # Redis DB 0) at call/construction time. Both sides of the confirm-url
    # handshake below happened to use that same dev-bound singleton
    # consistently, so this passed by accident rather than by being isolated
    # — and failed once observed deep in a large combined run, when an
    # earlier test's now-closed event loop left the shared connection dead.
    # Redirect to the test fixture's isolated client instead.
    monkeypatch.setattr(recorder_session, "redis_client", redis)
    monkeypatch.setattr(runner_module, "redis_client", redis)


def _find_ref(text: str, label: str) -> str:
    for line in text.splitlines():
        if label in line:
            match = _REF_RE.search(line)
            if match:
                return match.group(1)
    raise AssertionError(f"no ref found for label {label!r} in:\n{text}")


def _observation_text(messages: list[dict]) -> str:
    """All observation text seen so far, most recent last.

    mark_target's tool result is a bare confirmation with no fresh
    observation (dispatch() only re-observes for state-changing actions), so
    the ref list a turn needs can live in an EARLIER message. A real LLM
    reasons over the full history; this mirrors that instead of only reading
    the last message, which would miss refs from before a mark_target call.
    """
    parts = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part["text"])
    return "\n".join(parts)


def _make_scripted_complete_tools(drive_value: str):
    """A fake LLM that drives search.html the way the real agent should:
    fill -> submit -> mark the row -> mark title -> mark price -> done. Refs
    are read out of the real observation text at each turn, not hardcoded."""
    state = {"turn": 0, "row_ref": None}

    async def _fake(system, messages, tools, **kwargs):
        state["turn"] += 1
        text = _observation_text(messages)
        turn = state["turn"]

        if turn == 1:
            ref = _find_ref(text, "<input")
            return TurnResult([ToolCall("c1", "fill", {"ref": ref, "value": drive_value})], None, 5)
        if turn == 2:
            ref = _find_ref(text, "<button")
            return TurnResult([ToolCall("c2", "click", {"ref": ref})], None, 5)
        if turn == 3:
            ref = _find_ref(text, "li.product")
            state["row_ref"] = ref
            return TurnResult([ToolCall("c3", "mark_target", {"ref": ref})], None, 5)
        if turn == 4:
            ref = _find_ref(text, f"field inside {state['row_ref']}")
            return TurnResult([ToolCall("c4", "mark_target", {"ref": ref})], None, 5)
        if turn == 5:
            # second field leaf under the same row, excluding the one just marked
            lines = [ln for ln in text.splitlines() if f"field inside {state['row_ref']}" in ln]
            second = lines[1] if len(lines) > 1 else lines[0]
            ref = _REF_RE.search(second).group(1)
            return TurnResult([ToolCall("c5", "mark_target", {"ref": ref})], None, 5)
        return TurnResult([ToolCall("c6", "done", {})], None, 5)

    return _fake


def _fake_plan(fixture_site_url: str) -> dict:
    return {
        "url": f"{fixture_site_url}/search.html",
        "summary": "Search the fixture store",
        "parameters": [{
            "name": "query", "type": "string", "required": True,
            "drive_value": "television", "verify_value": "fan",
            "description": "Search term",
        }],
        "fields": [{"name": "title", "type": "string"}, {"name": "price", "type": "string"}],
    }


async def _funded_pro_user(db, make_user):
    user = await make_user()
    db.add(Subscription(
        user_id=user.id, tier=PlanTier.PRO, status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_run_agent_reaches_succeeded_against_the_fixture_site(
    db, make_user, redis, fixture_site_url,
):
    user = await _funded_pro_user(db, make_user)

    # Goes through the real gate+debit path (agent_runs.create_run), not a
    # bare AgentRun(...) construction — run_agent itself never debits; the
    # charge happens at creation time (the FastAPI route in Task 15), so
    # constructing the row directly would silently skip billing entirely and
    # make the balance assertion below vacuous.
    run = await agent_runs.create_run(user, "search the fixture store for products", db)
    await db.commit()
    assert run.status == AgentRunStatus.PLANNING

    plan = _fake_plan(fixture_site_url)
    fake_complete_tools = _make_scripted_complete_tools(plan["parameters"][0]["drive_value"])

    async def _confirm_repeatedly():
        # Redis pub/sub has no backlog: a message published before the
        # subscriber's SUBSCRIBE is acknowledged is simply lost. run_agent
        # reaches await_url_confirmation's subscribe call after a plan build,
        # two DB writes, and a status publish, so a single fixed-delay publish
        # races that and can miss the window entirely. Publish repeatedly
        # instead — cheap, and only one delivery needs to land inside an
        # active subscription for run_agent to proceed. Uses the `redis`
        # fixture directly — run_agent's own redis_client is monkeypatched to
        # the same instance, so this reaches the same channel it listens on.
        message = json.dumps({"t": "confirm_url", "ok": True})
        for _ in range(30):
            await redis.publish(cmd_channel(run.id), message)
            await asyncio.sleep(0.3)

    with patch("app.agent.runner.build_plan", AsyncMock(return_value=plan)), \
         patch("app.agent.driver.complete_tools", fake_complete_tools):
        confirm_task = asyncio.create_task(_confirm_repeatedly())
        await run_agent(run.id)
        confirm_task.cancel()

    await db.refresh(run)
    assert run.status == AgentRunStatus.SUCCEEDED, run.failure_reason
    assert run.workflow_id is not None

    workflow = await db.get(Workflow, run.workflow_id)
    assert workflow.status == WorkflowStatus.READY
    assert workflow.parameters and workflow.parameters[0]["name"] == "query"
    assert workflow.extraction.get("main")

    # The workflow must actually work: replay it with a value the agent never
    # drove with and confirm the parameter really changes the output.
    from app.recorder.replay import replay_workflow

    result = await replay_workflow(
        {"steps": workflow.steps, "extraction": workflow.extraction},
        {"query": "refrigerator"}, None, run.id, headless=True,
    )
    titles = {row["title"] for row in result["data"]}
    assert titles == {"Blue Refrigerator", "Silver Refrigerator"}

    # And the charge was kept (a successful run is not refunded).
    balance, _ = await wallet.balances(user.id, db)
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert balance == Decimal("100.00") - price


@pytest.mark.asyncio
async def test_run_agent_stops_after_one_attempt_on_a_blocked_site(
    db, make_user, redis, fixture_site_url,
):
    """A bot wall is unretryable: no change of strategy gets past it, so the
    repair loop must not spend two more attempts (and two more sets of LLM
    calls) reproducing the same failure. The model is never called at all —
    the driver's pre-flight check ends the attempt first.
    """
    user = await _funded_pro_user(db, make_user)
    run = await agent_runs.create_run(user, "search the blocked store for products", db)
    await db.commit()

    plan = {**_fake_plan(fixture_site_url), "url": f"{fixture_site_url}/cf-blocked.html"}
    never = AsyncMock(side_effect=AssertionError("the model was called on a block page"))

    async def _confirm_repeatedly():
        message = json.dumps({"t": "confirm_url", "ok": True})
        for _ in range(30):
            await redis.publish(cmd_channel(run.id), message)
            await asyncio.sleep(0.3)

    with patch("app.agent.runner.build_plan", AsyncMock(return_value=plan)), \
         patch("app.agent.driver.complete_tools", never):
        confirm_task = asyncio.create_task(_confirm_repeatedly())
        await run_agent(run.id)
        confirm_task.cancel()

    await db.refresh(run)
    assert run.status == AgentRunStatus.FAILED
    assert "blocking automated visits" in run.failure_reason
    assert run.attempt == 1, "a blocked site must not be retried"
    assert run.token_usage == 0
