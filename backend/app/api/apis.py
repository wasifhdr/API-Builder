import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.api import CustomApi, SpecStatus
from app.models.execution import ApiExecution
from app.models.user import User
from app.redis import redis_client
from app.schemas.api import ApiExecutionOut, CustomApiOut, CustomApiUpdate

router = APIRouter(prefix="/apis", tags=["apis"])


async def _get_owned_api(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi:
    api = await db.get(CustomApi, api_id)
    if api is None or api.owner_id != user.id:
        raise HTTPException(status_code=404, detail="api not found")
    return api


@router.get("", response_model=list[CustomApiOut])
async def list_apis(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CustomApi]:
    result = await db.execute(select(CustomApi).where(CustomApi.owner_id == user.id))
    return list(result.scalars().all())


@router.get("/{api_id}", response_model=CustomApiOut)
async def get_api(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    return await _get_owned_api(api_id, user, db)


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
