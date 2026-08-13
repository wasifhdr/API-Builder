import json
import logging
import time
import uuid

from app.agent.verify import VerifyResult
from app.db import async_session
from app.models.agent_run import AgentRun, AgentRunStatus
from app.redis import redis_client

log = logging.getLogger("agent")

MAX_ATTEMPTS = 3
WALL_CLOCK_SECONDS = 600

_STRATEGY_HINTS = {
    "differs_from_drive": (
        "The workflow returned identical data for a different parameter value. "
        "You almost certainly navigated straight to a result URL. This time, "
        "interact with the page: type into the search input and submit it."
    ),
    "has_rows": (
        "The extraction returned no rows. Try a different extraction target — "
        "mark the repeated result container, not a wrapper or a single field."
    ),
    "fields_present": (
        "Some declared fields were missing. Mark an element that actually "
        "contains all the requested data."
    ),
    "replays": (
        "The workflow failed to replay. Prefer stable, visible controls and "
        "avoid steps that depend on transient page state."
    ),
}


def should_retry(result: VerifyResult, attempt: int) -> bool:
    return not result.passed and attempt < MAX_ATTEMPTS


def repair_hint(result: VerifyResult) -> str:
    """Turns failed checks into a concrete change of strategy for the next
    attempt. Repeating the same route would just reproduce the same failure."""
    failed = [c for c in result.checks if not c.passed]
    if not failed:
        return ""
    parts = []
    for check in failed:
        parts.append(f"[{check.name}] {check.detail}")
        hint = _STRATEGY_HINTS.get(check.name)
        if hint:
            parts.append(hint)
    return "\n".join(parts)


def evt_channel(run_id: uuid.UUID) -> str:
    return f"agent:evt:{run_id}"


def cmd_channel(run_id: uuid.UUID) -> str:
    return f"agent:cmd:{run_id}"


async def publish(run_id: uuid.UUID, event: dict) -> None:
    await redis_client.publish(evt_channel(run_id), json.dumps(event))


async def _set_status(run_id: uuid.UUID, status: AgentRunStatus, **extra) -> None:
    async with async_session() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status
        for key, value in extra.items():
            setattr(run, key, value)
        await db.commit()
    await publish(run_id, {"t": "status", "state": status.value, **extra})


async def await_url_confirmation(run_id: uuid.UUID, timeout_s: float = 300.0) -> bool:
    """Blocks until the user confirms or rejects the resolved URL.

    Uses the same command-channel pattern the recorder already uses for
    pick-mode and undo — no new transport.
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(cmd_channel(run_id))
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if message is None or message["type"] != "message":
                continue
            command = json.loads(message["data"])
            if command.get("t") == "confirm_url":
                return bool(command.get("ok"))
        return False
    finally:
        await pubsub.unsubscribe(cmd_channel(run_id))
        await pubsub.aclose()


def build_repair_context(steps: list[dict], failure: str) -> str:
    """Renders the previous attempt for the repair prompt. Always goes through
    redact_steps: this is the one path where recorded credentials would
    otherwise reach the model, since drive-time values come from the plan
    rather than from the page."""
    from app.agent.distill import redact_steps

    safe = redact_steps(steps)
    lines = [f"Previous attempt failed: {failure}", "Steps taken:"]
    for i, step in enumerate(safe):
        value = (step.get("value") or {}).get("literal", "")
        lines.append(f"  {i}. {step.get('type')} {step.get('url', '')} {value}".rstrip())
    return "\n".join(lines)
