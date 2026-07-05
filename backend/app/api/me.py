from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user, get_effective_tier
from app.db import get_db
from app.models.user import User
from app.redis import redis_client
from app.schemas.user import MeOut, SettingsUpdate, UserOut
from app.services.plans import plan_for
from app.services.quota import get_usage_today

router = APIRouter(prefix="/me", tags=["me"])


async def _build_me_out(user: User, db: AsyncSession) -> MeOut:
    tier = await get_effective_tier(user.id, db)
    limit = plan_for(tier).daily_creation_limit
    used = await get_usage_today(user.id, redis_client, db)
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        tier=tier,
        quota_used_today=used,
        quota_limit=limit,
    )


@router.get("", response_model=MeOut)
async def get_me(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MeOut:
    return await _build_me_out(user, db)


@router.patch("/settings", response_model=MeOut)
async def update_settings(
    body: SettingsUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MeOut:
    merged = {**user.settings, **body.model_dump(exclude_unset=True)}
    user.settings = merged
    await db.commit()
    await db.refresh(user)
    return await _build_me_out(user, db)
