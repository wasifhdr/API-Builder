import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.api import ApiKey
from app.models.user import User
from app.schemas.api_key import ApiKeyCreated, ApiKeyCreateRequest, ApiKeyOut

router = APIRouter(prefix="/keys", tags=["keys"])


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
    )
    return list(result.scalars().all())


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    body: ApiKeyCreateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    raw_key = f"ab_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    key = ApiKey(user_id=user.id, label=body.label, key_prefix=raw_key[:12], key_hash=key_hash)
    db.add(key)
    await db.commit()
    await db.refresh(key)

    return ApiKeyCreated(
        id=key.id,
        label=key.label,
        key_prefix=key.key_prefix,
        last_used_at=key.last_used_at,
        created_at=key.created_at,
        api_key=raw_key,
    )


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(status_code=404, detail="key not found")
    key.revoked_at = datetime.now(timezone.utc)
    await db.commit()
