from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_effective_tier
from app.models.user import User
from app.redis import redis_client
from app.schemas.user import MeOut, UserOut
from app.services.plans import plan_for
from app.services.quota import get_usage_today
from app.services.sessions import user_sessions_key


async def build_me_out(user: User, db: AsyncSession) -> MeOut:
    tier = await get_effective_tier(user.id, db)
    limit = plan_for(tier).daily_creation_limit
    used = await get_usage_today(user.id, redis_client, db)
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        tier=tier,
        quota_used_today=used,
        quota_limit=limit,
    )


async def delete_user(db: AsyncSession, user: User) -> None:
    """Delete a user and every trace of their sessions.

    Uses a Core DELETE (not `session.delete`) so the database's ON DELETE
    CASCADE foreign keys remove dependent rows (workflows, APIs, keys,
    grants, subscriptions, transactions) — an ORM-level delete would instead
    try to null out those child FKs, which fails for NOT NULL columns like
    `workflows.user_id`.
    """
    sessions_key = user_sessions_key(user.id)
    sids = await redis_client.smembers(sessions_key)

    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()

    if sids:
        await redis_client.delete(*(f"sess:{sid}" for sid in sids))
    await redis_client.delete(sessions_key)
