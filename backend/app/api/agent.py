import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runner import CANCEL_TTL_SECONDS, cancel_key, cmd_channel, valid_start_url
from app.core.deps import current_user
from app.db import get_db
from app.models.agent_run import TERMINAL_STATUSES, AgentRun
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

    url = None
    # `body.url is not None` — not truthy — distinguishes the field being
    # omitted (None: keep the planner's URL, e.g. the confirm-only flow that
    # sends no url key at all) from the field being submitted blank (a user
    # who cleared the input and hit Confirm). An empty/whitespace string must
    # fail validation like any other bad URL, not be silently ignored.
    if body.ok and body.url is not None:
        url = valid_start_url(body.url)
        if url is None:
            raise HTTPException(
                status_code=400,
                detail="Enter a full http:// or https:// address.",
            )

    await redis_client.publish(
        cmd_channel(run_id),
        json.dumps({"t": "confirm_url", "ok": body.ok, "url": url}),
    )


@router.post("/runs/{run_id}/cancel", status_code=204)
async def cancel_run(
    run_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Stops a run in progress, at any phase.

    The row is marked cancelled and refunded here rather than being left for
    the worker, so the user sees the run stop the instant they click instead of
    waiting out an in-flight model turn. The worker unwinds cooperatively at
    its next checkpoint; _set_status refuses to move a terminal run back into
    an in-progress state in the meantime.
    """
    run = await _owned_run(run_id, user, db)
    if run.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="This run has already finished.")

    await agent_runs.cancel_run(run, db)
    await db.commit()

    # Two signals, because the worker can be in either of two places: parked on
    # the confirmation gate, where it is listening to the command channel, or
    # mid-drive, where it only polls the flag between turns.
    await redis_client.set(cancel_key(run_id), "1", ex=CANCEL_TTL_SECONDS)
    await redis_client.publish(cmd_channel(run_id), json.dumps({"t": "cancel"}))
