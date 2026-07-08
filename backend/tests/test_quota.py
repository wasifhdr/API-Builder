from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.api import CustomApi
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.workflow import Workflow
from app.services.quota import (
    QuotaExceeded,
    consume_call_quota,
    consume_creation_quota,
    get_calls_this_month,
    get_usage_today,
)

DHAKA = ZoneInfo("Asia/Dhaka")


async def _make_workflow(db, user_id, created_at):
    wf = Workflow(user_id=user_id, name="test", start_url="https://example.com", created_at=created_at)
    db.add(wf)
    await db.commit()
    return wf


async def test_consume_under_limit_increments(db, redis, make_user):
    user = await make_user()
    assert await consume_creation_quota(user.id, limit=5, redis=redis, db=db) == 1
    assert await consume_creation_quota(user.id, limit=5, redis=redis, db=db) == 2


async def test_consume_at_limit_raises_and_leaves_counter_unchanged(db, redis, make_user):
    user = await make_user()
    for _ in range(5):
        await consume_creation_quota(user.id, limit=5, redis=redis, db=db)

    with pytest.raises(QuotaExceeded) as exc_info:
        await consume_creation_quota(user.id, limit=5, redis=redis, db=db)
    assert exc_info.value.limit == 5
    assert exc_info.value.used == 5

    usage = await get_usage_today(user.id, redis, db)
    assert usage == 5


async def test_unlimited_tier_never_raises(db, redis, make_user):
    user = await make_user()
    for _ in range(20):
        assert await consume_creation_quota(user.id, limit=None, redis=redis, db=db) == -1


async def test_midnight_rollover_resets_counter(db, redis, make_user):
    user = await make_user()
    day1 = datetime(2026, 7, 6, 23, 0, tzinfo=DHAKA)
    day2 = datetime(2026, 7, 7, 0, 30, tzinfo=DHAKA)

    for _ in range(5):
        await consume_creation_quota(user.id, limit=5, redis=redis, db=db, now=day1)
    with pytest.raises(QuotaExceeded):
        await consume_creation_quota(user.id, limit=5, redis=redis, db=db, now=day1)

    # A new Dhaka calendar day is a fresh counter, independent of day1's usage.
    used = await consume_creation_quota(user.id, limit=5, redis=redis, db=db, now=day2)
    assert used == 1


async def test_redis_flush_falls_back_to_postgres_count(db, redis, make_user):
    user = await make_user()
    now = datetime.now(DHAKA)
    for _ in range(3):
        await _make_workflow(db, user.id, created_at=now)

    # Redis has no counter for today (simulating a flush) — must reflect the
    # 3 workflows already created today, not silently reset to 0.
    usage = await get_usage_today(user.id, redis, db, now=now)
    assert usage == 3

    new_count = await consume_creation_quota(user.id, limit=5, redis=redis, db=db, now=now)
    assert new_count == 4


# --- monthly call quota (Phase W4) ---


async def _make_api(db, user):
    workflow = Workflow(user_id=user.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=user.id,
        slug=f"test-quota-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def _make_execution(db, api_id, caller_id, created_at):
    execution = ApiExecution(
        api_id=api_id, caller_user_id=caller_id, params={}, status=ExecutionStatus.SUCCEEDED,
        created_at=created_at,
    )
    db.add(execution)
    await db.commit()
    return execution


async def test_call_quota_under_limit_increments(db, redis, make_user):
    user = await make_user()
    assert await consume_call_quota(user.id, limit=5, redis=redis, db=db) == 1
    assert await consume_call_quota(user.id, limit=5, redis=redis, db=db) == 2


async def test_call_quota_at_limit_raises_and_leaves_counter_unchanged(db, redis, make_user):
    user = await make_user()
    for _ in range(5):
        await consume_call_quota(user.id, limit=5, redis=redis, db=db)

    with pytest.raises(QuotaExceeded) as exc_info:
        await consume_call_quota(user.id, limit=5, redis=redis, db=db)
    assert exc_info.value.limit == 5
    assert exc_info.value.used == 5

    usage = await get_calls_this_month(user.id, redis, db)
    assert usage == 5


async def test_call_quota_unlimited_tier_never_raises(db, redis, make_user):
    user = await make_user()
    for _ in range(20):
        assert await consume_call_quota(user.id, limit=None, redis=redis, db=db) == -1


async def test_call_quota_month_rollover_resets_counter(db, redis, make_user):
    user = await make_user()
    month1 = datetime(2026, 6, 30, 23, 0, tzinfo=DHAKA)
    month2 = datetime(2026, 7, 1, 0, 30, tzinfo=DHAKA)

    for _ in range(5):
        await consume_call_quota(user.id, limit=5, redis=redis, db=db, now=month1)
    with pytest.raises(QuotaExceeded):
        await consume_call_quota(user.id, limit=5, redis=redis, db=db, now=month1)

    used = await consume_call_quota(user.id, limit=5, redis=redis, db=db, now=month2)
    assert used == 1


async def test_call_quota_redis_flush_falls_back_to_postgres_count(db, redis, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner)
    now = datetime.now(DHAKA)
    for _ in range(3):
        await _make_execution(db, api.id, caller.id, created_at=now)

    usage = await get_calls_this_month(caller.id, redis, db, now=now)
    assert usage == 3

    new_count = await consume_call_quota(caller.id, limit=5, redis=redis, db=db, now=now)
    assert new_count == 4
