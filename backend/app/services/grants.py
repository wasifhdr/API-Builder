import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_effective_tier
from app.models.api import ApiAccessGrant, CustomApi
from app.models.user import User, UserRole
from app.services.plans import plan_for


async def has_access(api: CustomApi, user_id: uuid.UUID | None, db: AsyncSession) -> bool:
    """Owner always has access. A non-owner needs a live (non-revoked,
    non-expired) grant, AND the owner's *current* effective tier must still
    allow sharing — checked at call time, not materialized, so a lapsed
    owner subscription revokes access for grantees immediately and a
    renewal restores it with no data changes (Blueprint decision #6).
    A super-admin owner always allows sharing, regardless of tier."""
    if user_id is None:
        return False
    if user_id == api.owner_id:
        return True

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ApiAccessGrant).where(
            ApiAccessGrant.api_id == api.id,
            ApiAccessGrant.user_id == user_id,
            ApiAccessGrant.revoked_at.is_(None),
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        return False
    if grant.expires_at is not None and grant.expires_at <= now:
        return False

    owner = await db.get(User, api.owner_id)
    if owner is not None and owner.role == UserRole.SUPER_ADMIN:
        return True

    owner_tier = await get_effective_tier(api.owner_id, db)
    return (await plan_for(owner_tier, db)).can_share
