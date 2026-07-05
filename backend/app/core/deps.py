import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.user import User, UserRole
from app.redis import redis_client

SESSION_COOKIE = "ab_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600


async def get_session_user_id(ab_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> uuid.UUID:
    if not ab_session:
        raise HTTPException(status_code=401, detail="not authenticated")

    key = f"sess:{ab_session}"
    user_id = await redis_client.hget(key, "user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="session expired")

    await redis_client.expire(key, SESSION_TTL_SECONDS)
    return uuid.UUID(user_id)


async def current_user(
    user_id: uuid.UUID = Depends(get_session_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin only")
    return user


async def get_effective_tier(user_id: uuid.UUID, db: AsyncSession) -> PlanTier:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        )
    )
    sub = result.scalar_one_or_none()
    return sub.tier if sub else PlanTier.FREE


@dataclass
class UserWithTier:
    user: User
    tier: PlanTier


async def current_user_with_tier(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> UserWithTier:
    tier = await get_effective_tier(user.id, db)
    return UserWithTier(user=user, tier=tier)
