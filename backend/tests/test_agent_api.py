import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.runner import UrlDecision, cancel_key, cmd_channel, valid_start_url
from app.api import agent as agent_api
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.schemas.agent import AgentRunCreate, ConfirmUrlIn
from app.services.plans import invalidate_cache
from app.services import wallet


@pytest.fixture(autouse=True)
def _reset_plan_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture(autouse=True)
def _agent_api_uses_test_redis(redis, monkeypatch):
    # app.api.agent imports the module-level redis_client bound at import
    # time to the dev Redis DB, not the test DB the `redis` fixture uses —
    # same mismatch test_plans.py documents for recordings_api.
    monkeypatch.setattr(agent_api, "redis_client", redis)


async def _funded_pro(db, make_user, amount="100.00"):
    user = await make_user()
    db.add(Subscription(
        user_id=user.id, tier=PlanTier.PRO, status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await wallet.credit(user.id, Decimal(amount), "recharge", db)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_create_run_enqueues_a_job(db, make_user, redis):
    user = await _funded_pro(db, make_user)

    run = await agent_api.create_run(
        AgentRunCreate(prompt="search walton for fridges"), user=user, db=db
    )

    assert run.status == AgentRunStatus.PLANNING
    entries = await redis.xrange("jobs:agent")
    assert len(entries) == 1
    assert json.loads(entries[0][1]["payload"])["agent_run_id"] == str(run.id)


@pytest.mark.asyncio
async def test_create_run_debits_the_wallet(db, make_user, redis):
    user = await _funded_pro(db, make_user)

    await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)

    balance, _ = await wallet.balances(user.id, db)
    assert balance < Decimal("100.00")


@pytest.mark.asyncio
async def test_free_tier_is_rejected_with_403(db, make_user):
    user = await make_user()  # no subscription -> free tier
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_empty_wallet_is_rejected_with_402(db, make_user):
    user = await make_user()
    db.add(Subscription(
        user_id=user.id, tier=PlanTier.PRO, status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_a_rejected_create_leaves_no_orphan_run(db, make_user, engine):
    """Gate and debit are one operation: create_run flushes the AgentRun row
    to assign its id for the ledger, then debits, then the route commits —
    so a mid-flight InsufficientBalance leaves an INSERT flushed but never
    committed. In a real request get_db's `async with async_session()`
    rolls that back automatically when the exception propagates past the
    route; this test calls the route directly (like every other route test
    in this suite), bypassing that context manager, so it does the same
    rollback explicitly to assert what production actually guarantees."""
    from sqlalchemy import select

    user = await make_user()
    db.add(Subscription(
        user_id=user.id, tier=PlanTier.PRO, status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await db.commit()

    with pytest.raises(HTTPException):
        await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)

    # Query through a fresh session/connection rather than the one that saw
    # the exception: the flushed-but-uncommitted INSERT is visible to THAT
    # session until it rolls back, but was never committed, so any other
    # connection — exactly like the real one get_db would hand the next
    # request — never sees it at all. This is the guarantee that matters.
    async with async_sessionmaker(engine, expire_on_commit=False)() as fresh_db:
        rows = await fresh_db.execute(select(AgentRun).where(AgentRun.user_id == user.id))
        assert rows.scalars().all() == []


@pytest.mark.asyncio
async def test_confirm_publishes_to_the_command_channel(db, make_user, redis):
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()

    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(run.id))
    await asyncio.sleep(0.1)  # let the subscription register

    await agent_api.confirm_url(run.id, ConfirmUrlIn(ok=True), user=user, db=db)

    # get_message() makes exactly one poll attempt (redis-py does not loop
    # internally for the full timeout): if that poll reads the subscribe
    # confirmation itself, ignore_subscribe_messages swallows it and this ONE
    # call returns None even though the real message is queued right behind
    # it — so this must poll, not call get_message once.
    message = None
    for _ in range(10):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message is not None:
            break
    assert message is not None, "confirm_url command never arrived"
    assert json.loads(message["data"]) == {"t": "confirm_url", "ok": True, "url": None}
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_confirm_on_someone_elses_run_is_404(db, make_user):
    owner = await make_user()
    stranger = await make_user()
    run = AgentRun(user_id=owner.id, prompt="p")
    db.add(run)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.confirm_url(run.id, ConfirmUrlIn(ok=True), user=stranger, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_user_cannot_read_another_users_run(db, make_user):
    owner = await make_user()
    stranger = await make_user()
    run = AgentRun(user_id=owner.id, prompt="p")
    db.add(run)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.get_run(run.id, user=stranger, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_run_returns_the_owners_run(db, make_user):
    owner = await make_user()
    run = AgentRun(user_id=owner.id, prompt="p")
    db.add(run)
    await db.commit()

    out = await agent_api.get_run(run.id, user=owner, db=db)
    assert out.id == run.id


@pytest.mark.asyncio
async def test_unknown_run_is_404(db, make_user):
    user = await make_user()
    with pytest.raises(HTTPException) as exc:
        await agent_api.get_run(uuid.uuid4(), user=user, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_runs_returns_only_the_callers_runs_newest_first(db, make_user):
    owner = await make_user()
    other = await make_user()
    db.add(AgentRun(user_id=other.id, prompt="not mine"))
    db.add(AgentRun(user_id=owner.id, prompt="first"))
    await db.commit()
    db.add(AgentRun(user_id=owner.id, prompt="second"))
    await db.commit()

    out = await agent_api.list_runs(user=owner, db=db)
    assert [r.prompt for r in out] == ["second", "first"]


def test_valid_start_url_accepts_an_http_url():
    assert valid_start_url("https://waltonbd.com/search") == "https://waltonbd.com/search"


def test_valid_start_url_strips_surrounding_whitespace():
    assert valid_start_url("  https://waltonbd.com  ") == "https://waltonbd.com"


def test_valid_start_url_rejects_a_javascript_url():
    assert valid_start_url("javascript:alert(1)") is None


def test_valid_start_url_rejects_a_scheme_without_a_host():
    assert valid_start_url("https://") is None


def test_valid_start_url_rejects_an_overlong_url():
    assert valid_start_url("https://x.com/" + "a" * 2100) is None


def test_valid_start_url_passes_none_through():
    assert valid_start_url(None) is None


@pytest.mark.asyncio
async def test_confirm_publishes_an_edited_url(db, make_user, redis):
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(run.id))

    await agent_api.confirm_url(
        run.id, ConfirmUrlIn(ok=True, url="https://waltonbd.com/search"), user, db,
    )

    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message["type"] == "message":
            assert json.loads(message["data"]) == {
                "t": "confirm_url", "ok": True, "url": "https://waltonbd.com/search",
            }
            break
    else:
        pytest.fail("no confirm_url command was published")

    await pubsub.unsubscribe(cmd_channel(run.id))
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_cancel_marks_the_run_cancelled_and_refunds(db, make_user, redis):
    """The route terminates the row itself rather than leaving it to the
    worker: a user who clicks stop mid-drive must not watch 'Driving the
    browser' for another model turn before anything changes."""
    user = await _funded_pro(db, make_user)
    run = await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)
    charged, _ = await wallet.balances(user.id, db)

    await agent_api.cancel_run(run.id, user=user, db=db)

    await db.refresh(run)
    assert run.status == AgentRunStatus.CANCELLED
    refunded, _ = await wallet.balances(user.id, db)
    assert refunded > charged


@pytest.mark.asyncio
async def test_cancel_sets_the_flag_the_drive_loop_polls(db, make_user, redis):
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.DRIVING)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await agent_api.cancel_run(run.id, user=user, db=db)

    assert await redis.get(cancel_key(run.id)) == "1"


@pytest.mark.asyncio
async def test_cancel_publishes_to_the_command_channel(db, make_user, redis):
    """The flag alone would not reach a run parked on the confirmation gate —
    that one is blocked on pub/sub and polls nothing."""
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(run.id))
    await asyncio.sleep(0.1)

    await agent_api.cancel_run(run.id, user=user, db=db)

    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message["type"] == "message":
            assert json.loads(message["data"]) == {"t": "cancel"}
            break
    else:
        pytest.fail("no cancel command was published")

    await pubsub.aclose()


@pytest.mark.asyncio
async def test_cancelling_a_finished_run_is_409(db, make_user, redis):
    """Not a silent no-op: a succeeded run's workflow already exists, and the
    UI would otherwise show a cancelled badge over a perfectly good API."""
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.SUCCEEDED)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    with pytest.raises(HTTPException) as exc:
        await agent_api.cancel_run(run.id, user=user, db=db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_on_someone_elses_run_is_404(db, make_user, redis):
    owner = await make_user()
    stranger = await make_user()
    run = AgentRun(user_id=owner.id, prompt="p", status=AgentRunStatus.DRIVING)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    with pytest.raises(HTTPException) as exc:
        await agent_api.cancel_run(run.id, user=stranger, db=db)
    assert exc.value.status_code == 404

    await db.refresh(run)
    assert run.status == AgentRunStatus.DRIVING


@pytest.mark.asyncio
async def test_confirm_rejects_a_javascript_url_with_400(db, make_user):
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    with pytest.raises(HTTPException) as exc:
        await agent_api.confirm_url(
            run.id, ConfirmUrlIn(ok=True, url="javascript:alert(1)"), user, db,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_confirm_rejects_a_cleared_url_with_400(db, make_user):
    """A blank string means the user cleared the field and hit Confirm — that
    must be rejected like any other invalid URL, not silently treated as
    'no correction' (which is what an omitted url, i.e. None, means)."""
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    with pytest.raises(HTTPException) as exc:
        await agent_api.confirm_url(
            run.id, ConfirmUrlIn(ok=True, url=""), user, db,
        )
    assert exc.value.status_code == 400
