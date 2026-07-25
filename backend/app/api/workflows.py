import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import current_user
from app.db import get_db
from app.models.api import CustomApi
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.constants import VALUE_STEP_TYPES
from app.redis import redis_client
from app.schemas.api import CustomApiOut
from app.schemas.workflow import MarkParameterIn, WorkflowListItem, WorkflowOut, WorkflowUpdate
from app.services import authoring
from app.services.publish import publish_workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _get_owned_workflow(workflow_id: uuid.UUID, user: User, db: AsyncSession) -> Workflow:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None or workflow.user_id != user.id:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


async def _serialize_workflow(workflow: Workflow, db: AsyncSession) -> WorkflowOut:
    row = (
        await db.execute(
            select(CustomApi.id, CustomApi.slug).where(CustomApi.workflow_id == workflow.id)
        )
    ).first()
    base = WorkflowOut.model_validate(workflow)
    return base.model_copy(
        update={
            "published_api_id": row.id if row else None,
            "published_api_slug": row.slug if row else None,
        }
    )


@router.get("", response_model=list[WorkflowListItem])
async def list_workflows(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Workflow]:
    # Excludes archived (cancelled) workflows and ones already published —
    # publishing doesn't change the workflow's own status (it can stay
    # "ready" forever), so "not yet published" has to be a NOT EXISTS check
    # against custom_apis rather than a status value.
    result = await db.execute(
        select(Workflow)
        .where(Workflow.user_id == user.id)
        .where(Workflow.status != WorkflowStatus.ARCHIVED)
        .where(~select(CustomApi.id).where(CustomApi.workflow_id == Workflow.id).exists())
        .order_by(Workflow.updated_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    return await _serialize_workflow(await _get_owned_workflow(workflow_id, user, db), db)


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    workflow = await _get_owned_workflow(workflow_id, user, db)
    data = body.model_dump(exclude_unset=True)
    if data.get("name"):
        workflow.name = data["name"]
    if data.get("parameters") is not None:
        workflow.parameters = data["parameters"]
    if data.get("extraction") is not None:
        workflow.extraction = data["extraction"]
    await db.commit()
    await db.refresh(workflow)
    return await _serialize_workflow(workflow, db)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    workflow = await _get_owned_workflow(workflow_id, user, db)
    published = await db.execute(select(CustomApi.id).where(CustomApi.workflow_id == workflow.id))
    if published.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="Unpublish the API before deleting its workflow")
    await db.delete(workflow)
    await db.commit()


@router.post("/{workflow_id}/publish", response_model=CustomApiOut, status_code=201)
async def publish(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    workflow = await _get_owned_workflow(workflow_id, user, db)
    if workflow.status != WorkflowStatus.READY:
        raise HTTPException(status_code=400, detail="workflow must be ready (needs extraction) to publish")
    return await publish_workflow(workflow, db)


@router.post("/{workflow_id}/suggest-parameters", status_code=202)
async def request_parameter_suggestions(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Queues the same LLM suggestion the recorder offers in-session, for a
    workflow whose browser has already closed. Poll the GET for the result."""
    workflow = await _get_owned_workflow(workflow_id, user, db)
    if not settings.llm_enabled:
        raise HTTPException(status_code=503, detail="AI suggestions are disabled on this server.")
    if not workflow.steps:
        raise HTTPException(status_code=400, detail="this workflow has no recorded steps yet")
    if workflow.status == WorkflowStatus.RECORDING:
        # The live session answers suggest_authoring over the WS and can also
        # name extraction fields; queuing a second, weaker one would race it.
        raise HTTPException(status_code=409, detail="ask the running recorder for suggestions instead")

    # Written before the enqueue so a poll landing in the gap sees "pending"
    # rather than "idle" and gives up.
    key = authoring.suggestions_key(workflow.id)
    await redis_client.set(key, json.dumps({"state": "pending"}), ex=authoring.RESULT_TTL_SECONDS)
    await redis_client.xadd(
        "jobs:llm",
        {"payload": json.dumps({"kind": authoring.JOB_KIND, "workflow_id": str(workflow.id)})},
    )
    return {"state": "pending"}


@router.get("/{workflow_id}/suggest-parameters")
async def get_parameter_suggestions(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _get_owned_workflow(workflow_id, user, db)  # ownership check
    raw = await redis_client.get(authoring.suggestions_key(workflow_id))
    if raw is None:
        return {"state": "idle"}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"state": "idle"}


@router.post("/{workflow_id}/mark-parameter", response_model=WorkflowOut)
async def mark_parameter(
    workflow_id: uuid.UUID,
    body: MarkParameterIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkflowOut:
    """Out-of-session `mark_param`: swaps a recorded literal for a named
    parameter. Mirrors RecordingSession._handle_mark_param, including reusing a
    name (the last marking of a name wins)."""
    workflow = await _get_owned_workflow(workflow_id, user, db)
    if workflow.status == WorkflowStatus.RECORDING:
        raise HTTPException(status_code=409, detail="mark parameters from the running recorder instead")

    # JSONB columns are replaced, never mutated in place — copy before editing.
    steps = [dict(s) for s in workflow.steps]
    if body.step_i >= len(steps):
        raise HTTPException(status_code=404, detail="no such step")

    step = steps[body.step_i]
    value = step.get("value")
    if step.get("type") not in VALUE_STEP_TYPES or not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="this step has no value to parameterize")
    if "literal" not in value:
        raise HTTPException(status_code=400, detail="this step is already a parameter")

    step["value"] = {"param": body.name}
    workflow.steps = steps
    workflow.parameters = [
        *(p for p in workflow.parameters if p.get("name") != body.name),
        {
            "name": body.name,
            "type": body.type,
            "required": True,
            "example": value["literal"],
            "description": body.description,
            "source_step": body.step_i,
        },
    ]
    await db.commit()
    await db.refresh(workflow)
    return await _serialize_workflow(workflow, db)


@router.post("/{workflow_id}/rerecord", status_code=202)
async def rerecord(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    workflow = await _get_owned_workflow(workflow_id, user, db)
    if workflow.status == WorkflowStatus.RECORDING:
        raise HTTPException(status_code=409, detail="this recording is already in progress")
    # Re-recording an existing API is not a new creation — no quota is consumed.
    # The live API keeps serving its snapshot until the owner syncs afterward.
    workflow.status = WorkflowStatus.RECORDING
    await db.commit()
    await redis_client.xadd(
        "jobs:rec",
        {"payload": json.dumps({
            "workflow_id": str(workflow.id),
            "user_id": str(user.id),
            "rerecord": True,
        })},
    )
    return {"ok": True}
