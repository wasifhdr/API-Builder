from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_effective_tier
from app.models.user import User, UserRole
from app.redis import redis_client
from app.schemas.user import MeOut, UserOut
from app.services.plans import plan_for
from app.services.quota import get_usage_today


async def build_me_out(user: User, db: AsyncSession) -> MeOut:
    tier = await get_effective_tier(user.id, db)
    is_super = user.role == UserRole.SUPER_ADMIN
    limit = None if is_super else (await plan_for(tier, db)).daily_creation_limit
    used = await get_usage_today(user.id, redis_client, db)
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        tier=tier,
        quota_used_today=used,
        quota_limit=limit,
    )
