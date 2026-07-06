import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.schemas.api import CustomApiOut
from app.schemas.workflow import WorkflowOut, WorkflowUpdate
from app.services.publish import publish_workflow

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _get_owned_workflow(workflow_id: uuid.UUID, user: User, db: AsyncSession) -> Workflow:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None or workflow.user_id != user.id:
        raise HTTPException(status_code=404, detail="workflow not found")
    return workflow


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Workflow:
    return await _get_owned_workflow(workflow_id, user, db)


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Workflow:
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
    return workflow


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
