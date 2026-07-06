import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api import apis as apis_api
from app.config import settings
from app.models.api import ApiVisibility, CustomApi
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.user import UserRole
from app.models.workflow import Workflow

DHAKA = ZoneInfo(settings.quota_tz)


async def _make_api(db, owner, *, visibility=ApiVisibility.PRIVATE, price_bdt=None):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=visibility,
        price_bdt=price_bdt,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


def _dhaka_midday(days_ago: int) -> datetime:
    """A UTC datetime that falls at midday on `days_ago` days before today,
    Dhaka-calendar-wise — safely inside a single Dhaka day regardless of DST-free
    +6:00 offset math."""
    today_dhaka = datetime.now(DHAKA).date()
    target_day = today_dhaka - timedelta(days=days_ago)
    local_noon = datetime(target_day.year, target_day.month, target_day.day, 12, 0, 0, tzinfo=DHAKA)
    return local_noon.astimezone(timezone.utc)


async def _add_execution(
    db,
    api,
    *,
    status=ExecutionStatus.SUCCEEDED,
    caller_user_id=None,
    cache_hit=False,
    duration_ms=None,
    days_ago=0,
):
    execution = ApiExecution(
        api_id=api.id,
        caller_user_id=caller_user_id,
        status=status,
        cache_hit=cache_hit,
        duration_ms=duration_ms,
        created_at=_dhaka_midday(days_ago),
    )
    db.add(execution)
    await db.commit()
    return execution


# --- access control ---


async def test_owner_can_view_stats(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)
    stats = await apis_api.get_api_stats(api_id=api.id, user=owner, db=db)
    assert stats.total_calls == 0


async def test_unrelated_user_gets_404(db, make_user):
    owner = await make_user()
    other = await make_user()
    api = await _make_api(db, owner)
    with pytest.raises(HTTPException) as exc_info:
        await apis_api.get_api_stats(api_id=api.id, user=other, db=db)
    assert exc_info.value.status_code == 404


async def test_super_admin_can_view_stats(db, make_user):
    owner = await make_user()
    admin = await make_user()
    admin.role = UserRole.SUPER_ADMIN
    db.add(admin)
    await db.commit()
    api = await _make_api(db, owner)
    stats = await apis_api.get_api_stats(api_id=api.id, user=admin, db=db)
    assert stats.total_calls == 0


async def test_missing_api_returns_404(db, make_user):
    owner = await make_user()
    with pytest.raises(HTTPException) as exc_info:
        await apis_api.get_api_stats(api_id=uuid.uuid4(), user=owner, db=db)
    assert exc_info.value.status_code == 404


# --- never called ---


async def test_never_called_api_returns_zeros(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)
    stats = await apis_api.get_api_stats(api_id=api.id, user=owner, db=db)
    assert stats.total_calls == 0
    assert stats.calls_7d == 0
    assert stats.success_rate_7d == 0
    assert stats.avg_duration_ms_7d is None
    assert stats.cache_hit_rate_7d == 0
    assert len(stats.calls_by_day) == 14
    assert all(day.total == 0 and day.succeeded == 0 for day in stats.calls_by_day)
    assert stats.top_consumers == []
    assert stats.last_called_at is None


# --- aggregation correctness ---


async def test_stats_aggregate_totals_and_rates(db, make_user):
    owner = await make_user()
    caller1 = await make_user(email="caller1@example.com")
    caller2 = await make_user(email="caller2@example.com")
    caller2.username = "caller2name"
    db.add(caller2)
    await db.commit()

    api = await _make_api(db, owner)

    # Within the 7d/30d windows: two succeeded (one cache hit), one failed.
    await _add_execution(
        db, api, status=ExecutionStatus.SUCCEEDED, caller_user_id=caller1.id,
        cache_hit=True, duration_ms=100, days_ago=0,
    )
    await _add_execution(
        db, api, status=ExecutionStatus.SUCCEEDED, caller_user_id=caller2.id,
        cache_hit=False, duration_ms=300, days_ago=1,
    )
    await _add_execution(
        db, api, status=ExecutionStatus.FAILED, caller_user_id=caller2.id,
        cache_hit=False, duration_ms=None, days_ago=2,
    )
    # Null caller — must be skipped from top_consumers but still counted in totals.
    await _add_execution(
        db, api, status=ExecutionStatus.SUCCEEDED, caller_user_id=None,
        cache_hit=False, duration_ms=50, days_ago=3,
    )
    # Outside the 7d window (10 days ago) but inside the 14-day bucket range and 30d window.
    await _add_execution(
        db, api, status=ExecutionStatus.SUCCEEDED, caller_user_id=caller1.id,
        cache_hit=False, duration_ms=200, days_ago=10,
    )
    # Outside every window (40 days ago) — counts only toward total_calls/last_called_at floor.
    await _add_execution(
        db, api, status=ExecutionStatus.FAILED, caller_user_id=caller1.id,
        cache_hit=False, duration_ms=None, days_ago=40,
    )

    stats = await apis_api.get_api_stats(api_id=api.id, user=owner, db=db)

    assert stats.total_calls == 6
    assert stats.calls_7d == 4  # days_ago 0,1,2,3
    assert stats.success_rate_7d == pytest.approx(3 / 4)
    assert stats.cache_hit_rate_7d == pytest.approx(1 / 4)
    # avg over rows with duration_ms not null within 7d: 100, 300, 50 -> 150
    assert stats.avg_duration_ms_7d == pytest.approx(150.0)
    assert stats.last_called_at is not None

    assert len(stats.calls_by_day) == 14
    assert stats.calls_by_day[-1].total == 1  # today
    assert stats.calls_by_day[-1].succeeded == 1
    assert stats.calls_by_day[-3].total == 1  # 2 days ago -> failed
    assert stats.calls_by_day[-3].succeeded == 0

    # top_consumers: within 30d window, null caller skipped.
    # caller1: days_ago 0 and 10 -> 2 calls (40 days ago is outside 30d window)
    # caller2: days_ago 1 and 2 -> 2 calls
    names = {c.name: c.calls_30d for c in stats.top_consumers}
    assert names.get(caller1.email) == 2
    assert names.get("caller2name") == 2  # username fallback preferred over email
    assert len(stats.top_consumers) == 2


async def test_top_consumers_ordering_and_limit(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)

    callers = [await make_user(email=f"c{i}@example.com") for i in range(6)]
    # Give each caller a distinct call count so ordering is unambiguous, all within 30d.
    for idx, caller in enumerate(callers):
        count = idx + 1  # 1..6 calls
        for _ in range(count):
            await _add_execution(db, api, caller_user_id=caller.id, days_ago=1)

    stats = await apis_api.get_api_stats(api_id=api.id, user=owner, db=db)

    assert len(stats.top_consumers) == 5
    counts = [c.calls_30d for c in stats.top_consumers]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 6
    # The caller with only 1 call (lowest) must be excluded since only top 5 fit.
    assert 1 not in counts


async def test_username_fallback_to_email(db, make_user):
    owner = await make_user()
    caller = await make_user(email="nousername@example.com")
    assert caller.username is None
    api = await _make_api(db, owner)
    await _add_execution(db, api, caller_user_id=caller.id, days_ago=0)

    stats = await apis_api.get_api_stats(api_id=api.id, user=owner, db=db)
    assert stats.top_consumers[0].name == "nousername@example.com"


async def test_calls_by_day_zero_fill_and_order(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)
    await _add_execution(db, api, days_ago=0)
    await _add_execution(db, api, days_ago=13)

    stats = await apis_api.get_api_stats(api_id=api.id, user=owner, db=db)
    assert len(stats.calls_by_day) == 14
    # Ascending date order: first entry is the oldest (13 days ago), last is today.
    dates = [day.date for day in stats.calls_by_day]
    assert dates == sorted(dates)
    assert stats.calls_by_day[0].total == 1
    assert stats.calls_by_day[-1].total == 1
    assert sum(day.total for day in stats.calls_by_day) == 2
