import uuid

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
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
