import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import UserWithTier, current_user_with_tier
from app.db import get_db
from app.models.workflow import Workflow, WorkflowStatus
from app.redis import redis_client
from app.schemas.recording import CreateRecordingRequest, CreateRecordingResponse
from app.services.plans import plan_for
from app.services.quota import QuotaExceeded, consume_creation_quota

router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.post("", response_model=CreateRecordingResponse, status_code=201)
async def create_recording(
    body: CreateRecordingRequest,
    ctx: UserWithTier = Depends(current_user_with_tier),
    db: AsyncSession = Depends(get_db),
) -> CreateRecordingResponse:
    if not ctx.is_super:
        limit = (await plan_for(ctx.tier, db)).daily_creation_limit
        try:
            await consume_creation_quota(ctx.user.id, limit, redis_client, db)
        except QuotaExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Daily API creation limit reached",
                    "limit": exc.limit,
                    "used": exc.used,
                    "reset_seconds": exc.reset_seconds,
                },
            ) from exc

    workflow = Workflow(
        user_id=ctx.user.id,
        name=body.name,
        start_url=body.start_url,
        status=WorkflowStatus.RECORDING,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    await redis_client.xadd(
        "jobs:rec",
        {"payload": json.dumps({"workflow_id": str(workflow.id), "user_id": str(ctx.user.id)})},
    )

    return CreateRecordingResponse(workflow_id=workflow.id)
