from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import SESSION_COOKIE, current_user
from app.db import get_db
from app.models.user import User
from app.redis import redis_client
from app.schemas.user import (
    DeleteAccountRequest,
    MeOut,
    PasswordSetRequest,
    ProfileUpdateRequest,
    SessionOut,
)
from app.services.accounts import build_me_out, delete_user
from app.services.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.services.sessions import user_sessions_key

router = APIRouter(prefix="/me", tags=["profile"])


@router.patch("/profile", response_model=MeOut)
async def update_profile(
    body: ProfileUpdateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> MeOut:
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        user.name = changes["name"]
    if "phone" in changes:
        user.phone = changes["phone"]
    await db.commit()
    await db.refresh(user)
    return await build_me_out(user, db)


@router.post("/password", response_model=MeOut)
async def set_password(
    body: PasswordSetRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
    current_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> MeOut:
    if len(body.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"password must be at least {MIN_PASSWORD_LENGTH} characters"
        )

    if user.password_hash is not None:
        if not body.current_password:
            raise HTTPException(status_code=400, detail="current password is required")
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="current password is incorrect")

    user.password_hash = hash_password(body.new_password)
    await db.commit()
    await db.refresh(user)

    # Revoke every other session, keeping the one making this request.
    sessions_key = user_sessions_key(user.id)
    sids = await redis_client.smembers(sessions_key)
    other_sids = [sid for sid in sids if sid != current_sid]
    if other_sids:
        await redis_client.delete(*(f"sess:{sid}" for sid in other_sids))
        await redis_client.srem(sessions_key, *other_sids)

    return await build_me_out(user, db)


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(current_user),
    current_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> list[SessionOut]:
    sessions_key = user_sessions_key(user.id)
    sids = await redis_client.smembers(sessions_key)

    out: list[SessionOut] = []
    stale: list[str] = []
    for sid in sids:
        data = await redis_client.hgetall(f"sess:{sid}")
        if not data:
            stale.append(sid)
            continue
        out.append(
            SessionOut(
                sid_prefix=sid[:8],
                created_at=data["created_at"],
                user_agent=data.get("user_agent") or None,
                ip=data.get("ip") or None,
                current=(sid == current_sid),
            )
        )

    if stale:
        await redis_client.srem(sessions_key, *stale)

    out.sort(key=lambda s: s.created_at, reverse=True)
    return out


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(
    user: User = Depends(current_user),
    current_sid: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    sessions_key = user_sessions_key(user.id)
    sids = await redis_client.smembers(sessions_key)
    other_sids = [sid for sid in sids if sid != current_sid]
    if other_sids:
        await redis_client.delete(*(f"sess:{sid}" for sid in other_sids))
        await redis_client.srem(sessions_key, *other_sids)
    return {"revoked": len(other_sids)}


@router.delete("")
async def delete_account(
    body: DeleteAccountRequest,
    response: Response,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if body.confirm_username != user.username:
        raise HTTPException(status_code=400, detail="username confirmation does not match")

    if user.password_hash is not None:
        if not body.current_password:
            raise HTTPException(status_code=400, detail="current password is required")
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="current password is incorrect")

    await delete_user(db, user)

    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
