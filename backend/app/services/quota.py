import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.execution import ApiExecution
from app.models.workflow import Workflow

QUOTA_KEY_TTL_SECONDS = 48 * 3600
MONTH_QUOTA_KEY_TTL_SECONDS = 32 * 24 * 3600  # covers the longest possible month + buffer


class QuotaExceeded(Exception):
    def __init__(self, limit: int, used: int, reset_seconds: int):
        self.limit = limit
        self.used = used
        self.reset_seconds = reset_seconds
        super().__init__(f"quota exceeded: {used}/{limit}, resets in {reset_seconds}s")


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.quota_tz)


def quota_key(user_id: uuid.UUID, now: datetime | None = None) -> str:
    now = now or datetime.now(_tz())
    return f"quota:create:{user_id}:{now.strftime('%Y%m%d')}"


def _day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(_tz())
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_until_reset(now: datetime | None = None) -> int:
    now = now or datetime.now(_tz())
    next_day_start = _day_start(now) + timedelta(days=1)
    return int((next_day_start - now).total_seconds())


async def get_usage_today(
    user_id: uuid.UUID, redis: Redis, db: AsyncSession, now: datetime | None = None,
) -> int:
    key = quota_key(user_id, now)
    cached = await redis.get(key)
    if cached is not None:
        return int(cached)

    # Redis was flushed/missing this key — fall back to counting today's
    # workflow rows in Postgres, then reseed the cache with that truth.
    start = _day_start(now)
    result = await db.execute(
        select(func.count()).select_from(Workflow).where(
            Workflow.user_id == user_id, Workflow.created_at >= start,
        )
    )
    count = result.scalar_one()
    await redis.set(key, count, ex=QUOTA_KEY_TTL_SECONDS)
    return count


async def consume_creation_quota(
    user_id: uuid.UUID,
    limit: int | None,
    redis: Redis,
    db: AsyncSession,
    now: datetime | None = None,
) -> int:
    """Increments today's creation-attempt counter.

    limit=None means unlimited (Max tier) — the counter isn't touched.
    Raises QuotaExceeded (leaving the counter unchanged) if the limit is hit.
    """
    if limit is None:
        return -1

    # Ensure Redis reflects Postgres truth before incrementing, in case the
    # key was flushed/expired.
    await get_usage_today(user_id, redis, db, now)

    key = quota_key(user_id, now)
    new_count = await redis.incr(key)
    await redis.expire(key, QUOTA_KEY_TTL_SECONDS)
    if new_count > limit:
        await redis.decr(key)
        raise QuotaExceeded(limit=limit, used=new_count - 1, reset_seconds=seconds_until_reset(now))
    return new_count


def call_quota_key(user_id: uuid.UUID, now: datetime | None = None) -> str:
    now = now or datetime.now(_tz())
    return f"quota:calls:{user_id}:{now.strftime('%Y%m')}"


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(_tz())
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(now: datetime | None = None) -> datetime:
    start = _month_start(now)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def seconds_until_month_reset(now: datetime | None = None) -> int:
    now = now or datetime.now(_tz())
    return int((_next_month_start(now) - now).total_seconds())


async def get_calls_this_month(
    user_id: uuid.UUID, redis: Redis, db: AsyncSession, now: datetime | None = None,
) -> int:
    key = call_quota_key(user_id, now)
    cached = await redis.get(key)
    if cached is not None:
        return int(cached)

    start = _month_start(now)
    result = await db.execute(
        select(func.count()).select_from(ApiExecution).where(
            ApiExecution.caller_user_id == user_id, ApiExecution.created_at >= start,
        )
    )
    count = result.scalar_one()
    await redis.set(key, count, ex=MONTH_QUOTA_KEY_TTL_SECONDS)
    return count


async def consume_call_quota(
    user_id: uuid.UUID,
    limit: int | None,
    redis: Redis,
    db: AsyncSession,
    now: datetime | None = None,
) -> int:
    """Increments this calendar month's call counter (Asia/Dhaka).

    limit=None means unlimited (no cap tier) — the counter isn't touched.
    Raises QuotaExceeded (leaving the counter unchanged) if the limit is hit.
    """
    if limit is None:
        return -1

    await get_calls_this_month(user_id, redis, db, now)

    key = call_quota_key(user_id, now)
    new_count = await redis.incr(key)
    await redis.expire(key, MONTH_QUOTA_KEY_TTL_SECONDS)
    if new_count > limit:
        await redis.decr(key)
        raise QuotaExceeded(limit=limit, used=new_count - 1, reset_seconds=seconds_until_month_reset(now))
    return new_count


def api_subscription_quota_key(
    user_id: uuid.UUID, api_id: uuid.UUID, now: datetime | None = None,
) -> str:
    now = now or datetime.now(_tz())
    return f"quota:api-included:{user_id}:{api_id}:{now.strftime('%Y%m')}"


async def get_api_calls_this_month(
    user_id: uuid.UUID, api_id: uuid.UUID, redis: Redis, db: AsyncSession, now: datetime | None = None,
) -> int:
    key = api_subscription_quota_key(user_id, api_id, now)
    cached = await redis.get(key)
    if cached is not None:
        return int(cached)

    start = _month_start(now)
    result = await db.execute(
        select(func.count()).select_from(ApiExecution).where(
            ApiExecution.caller_user_id == user_id,
            ApiExecution.api_id == api_id,
            ApiExecution.created_at >= start,
        )
    )
    count = result.scalar_one()
    await redis.set(key, count, ex=MONTH_QUOTA_KEY_TTL_SECONDS)
    return count


async def consume_api_subscription_quota(
    user_id: uuid.UUID,
    api_id: uuid.UUID,
    limit: int | None,
    redis: Redis,
    db: AsyncSession,
    now: datetime | None = None,
) -> int:
    """Increments this calendar month's included-call counter for one
    subscription-mode API grant (Phase W6).

    limit=None means unlimited included calls — the counter isn't touched.
    Raises QuotaExceeded (leaving the counter unchanged) if the limit is hit.
    """
    if limit is None:
        return -1

    await get_api_calls_this_month(user_id, api_id, redis, db, now)

    key = api_subscription_quota_key(user_id, api_id, now)
    new_count = await redis.incr(key)
    await redis.expire(key, MONTH_QUOTA_KEY_TTL_SECONDS)
    if new_count > limit:
        await redis.decr(key)
        raise QuotaExceeded(limit=limit, used=new_count - 1, reset_seconds=seconds_until_month_reset(now))
    return new_count
