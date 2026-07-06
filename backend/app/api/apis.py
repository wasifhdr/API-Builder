import json
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user, get_effective_tier
from app.db import get_db
from app.models.api import ApiAccessGrant, ApiInvite, ApiVisibility, CustomApi, SpecStatus
from app.models.billing import PlanTier
from app.models.execution import ApiExecution
from app.models.user import User
from app.redis import redis_client
from app.schemas.api import ApiExecutionOut, CustomApiOut, CustomApiUpdate
from app.schemas.invite import CreateInviteRequest, GrantOut, InviteOut
from app.services.grants import has_access

router = APIRouter(prefix="/apis", tags=["apis"])


async def _get_owned_api(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi:
    api = await db.get(CustomApi, api_id)
    if api is None or api.owner_id != user.id:
        raise HTTPException(status_code=404, detail="api not found")
    return api


async def _get_visible_api(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi:
    api = await db.get(CustomApi, api_id)
    if api is None or not await has_access(api, user.id, db):
        raise HTTPException(status_code=404, detail="api not found")
    return api


@router.get("", response_model=list[CustomApiOut])
async def list_apis(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CustomApi]:
    owned_result = await db.execute(select(CustomApi).where(CustomApi.owner_id == user.id))
    owned = list(owned_result.scalars().all())

    granted_result = await db.execute(
        select(CustomApi)
        .join(ApiAccessGrant, ApiAccessGrant.api_id == CustomApi.id)
        .where(ApiAccessGrant.user_id == user.id, ApiAccessGrant.revoked_at.is_(None))
    )
    granted = list(granted_result.scalars().all())

    seen = {a.id for a in owned}
    return owned + [a for a in granted if a.id not in seen]


@router.get("/{api_id}", response_model=CustomApiOut)
async def get_api(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    return await _get_visible_api(api_id, user, db)


@router.patch("/{api_id}", response_model=CustomApiOut)
async def update_api(
    api_id: uuid.UUID,
    body: CustomApiUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    api = await _get_owned_api(api_id, user, db)
    data = body.model_dump(exclude_unset=True)
    if data.get("cache_ttl_seconds") is not None:
        api.cache_ttl_seconds = data["cache_ttl_seconds"]
    if data.get("is_active") is not None:
        api.is_active = data["is_active"]

    wants_shared = data.get("visibility") == ApiVisibility.SHARED
    wants_price = bool(data.get("price_bdt")) and data["price_bdt"] > 0
    if wants_shared or wants_price:
        tier = await get_effective_tier(user.id, db)
        if tier not in (PlanTier.PRO, PlanTier.MAX):
            raise HTTPException(status_code=403, detail="sharing and pricing require a Pro or Max plan")

    if "visibility" in data:
        api.visibility = data["visibility"]
    if "price_bdt" in data:
        api.price_bdt = data["price_bdt"]

    await db.commit()
    await db.refresh(api)
    return api


@router.post("/{api_id}/regenerate-spec", response_model=CustomApiOut)
async def regenerate_spec(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    api = await _get_owned_api(api_id, user, db)
    api.spec_status = SpecStatus.PENDING
    await db.commit()
    await db.refresh(api)
    await redis_client.xadd("jobs:llm", {"payload": json.dumps({"api_id": str(api.id)})})
    return api


@router.get("/{api_id}/executions", response_model=list[ApiExecutionOut])
async def list_executions(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiExecution]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiExecution)
        .where(ApiExecution.api_id == api.id)
        .order_by(ApiExecution.created_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())


@router.post("/{api_id}/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    api_id: uuid.UUID,
    body: CreateInviteRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiInvite:
    api = await _get_owned_api(api_id, user, db)
    tier = await get_effective_tier(user.id, db)
    if tier not in (PlanTier.PRO, PlanTier.MAX):
        raise HTTPException(status_code=403, detail="invites require a Pro or Max plan")
    if api.visibility != ApiVisibility.SHARED:
        raise HTTPException(status_code=400, detail="set visibility to shared before creating invites")

    invite = ApiInvite(
        api_id=api.id,
        created_by=user.id,
        token=secrets.token_urlsafe(24),
        max_uses=body.max_uses,
        expires_at=body.expires_at,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.get("/{api_id}/invites", response_model=list[InviteOut])
async def list_invites(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiInvite]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiInvite).where(ApiInvite.api_id == api.id).order_by(ApiInvite.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{api_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    api_id: uuid.UUID,
    invite_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api = await _get_owned_api(api_id, user, db)
    invite = await db.get(ApiInvite, invite_id)
    if invite is None or invite.api_id != api.id:
        raise HTTPException(status_code=404, detail="invite not found")
    invite.revoked_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/{api_id}/grants", response_model=list[GrantOut])
async def list_grants(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiAccessGrant]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id).order_by(ApiAccessGrant.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{api_id}/grants/{grant_id}", status_code=204)
async def revoke_grant(
    api_id: uuid.UUID,
    grant_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api = await _get_owned_api(api_id, user, db)
    grant = await db.get(ApiAccessGrant, grant_id)
    if grant is None or grant.api_id != api.id:
        raise HTTPException(status_code=404, detail="grant not found")
    grant.revoked_at = datetime.now(timezone.utc)
    await db.commit()
