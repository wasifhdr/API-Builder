import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import cmd_channel
from app.core.deps import current_user
from app.db import get_db
from app.models.agent_run import AgentRun
from app.models.user import User
from app.redis import redis_client
from app.schemas.agent import AgentRunCreate, AgentRunOut, ConfirmUrlIn
from app.services import agent_runs, wallet

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/runs", response_model=AgentRunOut, status_code=202)
async def create_run(
    body: AgentRunCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRun:
    """Starts an autonomous authoring run. Gate and debit happen inside
    agent_runs.create_run as one operation — a 402/403 here never leaves an
    orphan run row, and commit only happens after that call succeeds."""
    try:
        run = await agent_runs.create_run(user, body.prompt, db)
    except agent_runs.AgentRunNotAllowed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except wallet.InsufficientBalance as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(run)

    await redis_client.xadd(
        "jobs:agent", {"payload": json.dumps({"agent_run_id": str(run.id)})},
    )
    return run


@router.get("/runs", response_model=list[AgentRunOut])
async def list_runs(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRun]:
    result = await db.execute(
        select(AgentRun).where(AgentRun.user_id == user.id).order_by(AgentRun.created_at.desc())
    )
    return list(result.scalars().all())


async def _owned_run(run_id: uuid.UUID, user: User, db: AsyncSession) -> AgentRun:
    run = await db.get(AgentRun, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status_code=404, detail="agent run not found")
    return run


@router.get("/runs/{run_id}", response_model=AgentRunOut)
async def get_run(
    run_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRun:
    return await _owned_run(run_id, user, db)


@router.post("/runs/{run_id}/confirm", status_code=204)
async def confirm_url(
    run_id: uuid.UUID,
    body: ConfirmUrlIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Answers run_agent's await_url_confirmation, which is blocked on this
    exact channel — same command-channel pattern the recorder uses for
    pick-mode and undo, no new transport."""
    await _owned_run(run_id, user, db)
    await redis_client.publish(cmd_channel(run_id), json.dumps({"t": "confirm_url", "ok": body.ok}))
