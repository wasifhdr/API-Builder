import secrets
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.core.deps import SESSION_TTL_SECONDS


def user_sessions_key(user_id: uuid.UUID) -> str:
    return f"user_sessions:{user_id}"


async def create_session(
    redis: Redis,
    user_id: uuid.UUID,
    *,
    user_agent: str | None,
    ip: str | None,
) -> str:
    sid = secrets.token_urlsafe(32)
    key = f"sess:{sid}"
    await redis.hset(
        key,
        mapping={
            "user_id": str(user_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_agent": (user_agent or "")[:300],
            "ip": ip or "",
        },
    )
    await redis.expire(key, SESSION_TTL_SECONDS)
    # No TTL on the index set: entries become stale once their sess:{sid} hash
    # expires, and are pruned lazily whenever the set is enumerated.
    await redis.sadd(user_sessions_key(user_id), sid)
    return sid


async def destroy_session(redis: Redis, sid: str, user_id: uuid.UUID) -> None:
    await redis.delete(f"sess:{sid}")
    await redis.srem(user_sessions_key(user_id), sid)
