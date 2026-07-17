import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.api import CustomApi
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.schemas.api import CustomApiOut
from app.schemas.workflow import WorkflowListItem, WorkflowOut, WorkflowUpdate
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
