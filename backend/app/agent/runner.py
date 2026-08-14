import json
import logging
import time
import uuid

from app.agent.distill import DistillError, append_extract_step, bind_parameters
from app.agent.driver import drive
from app.agent.extract import ExtractionError, build_extraction
from app.agent.planner import build_plan
from app.agent.verify import VerifyResult, missing_field_names, verify_workflow
from app.db import async_session
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.session import RecordingSession
from app.redis import redis_client
from app.services import agent_runs

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
    "stable_selectors": (
        "A step was anchored to text or a link captured from the first "
        "parameter value's results, so it can never match a different value. "
        "Do not click an individual result to reach the data — extract from "
        "the page the parameterized interaction itself produces."
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


def sample_failure_reason(sample: object, fields: list[dict] | None) -> str | None:
    """Rejects an attempt whose marked elements produced nothing, before a
    verify replay is spent on it.

    Applies exactly the rule verify applies to the replayed output: a sample of
    pure nulls is a broken extraction, not an empty result set. Catching it here
    saves a full headless replay on an attempt that cannot pass.
    """
    missing = missing_field_names(sample, fields)
    if not missing:
        return None
    return "the marked elements produced no value for: " + ", ".join(missing)


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


async def _finish(run_id: uuid.UUID, *, succeeded: bool, reason: str | None, tokens: int) -> None:
    async with async_session() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            return
        run.token_usage = tokens
        await db.commit()
        await agent_runs.finish_run(run, succeeded=succeeded, reason=reason, db=db)
        await db.commit()
    await publish(run_id, {
        "t": "status", "state": "succeeded" if succeeded else "failed", "reason": reason,
    })


async def run_agent(agent_run_id: uuid.UUID) -> None:
    """Drives an autonomous authoring run end to end: plan, confirm, then
    drive -> distill -> extract -> verify, repairing with a strategy hint on
    failure up to MAX_ATTEMPTS or WALL_CLOCK_SECONDS, whichever comes first.

    Runs entirely inside the worker's jobs:agent handler — this IS the
    recording session (it consumes the single recording slot), so it drives a
    real headless RecordingSession rather than talking to one over Redis.
    """
    started = time.monotonic()

    async with async_session() as db:
        run = await db.get(AgentRun, agent_run_id)
        if run is None:
            return
        user = await db.get(User, run.user_id)
        prompt = run.prompt
        user_id = run.user_id

    if user is None:
        await _finish(agent_run_id, succeeded=False, reason="user not found", tokens=0)
        return

    # --- Plan ---
    await _set_status(agent_run_id, AgentRunStatus.PLANNING)
    try:
        plan = await build_plan(prompt)
    except Exception as exc:  # noqa: BLE001 - any planning failure (PlanError or LLM error) ends the run
        log.warning("agent %s planning failed: %s", agent_run_id, exc)
        await _finish(agent_run_id, succeeded=False, reason=f"planning failed: {exc}", tokens=0)
        return

    async with async_session() as db:
        run = await db.get(AgentRun, agent_run_id)
        run.plan = plan
        run.resolved_url = plan["url"]
        await db.commit()

    # --- Await user confirmation of the resolved URL ---
    await _set_status(agent_run_id, AgentRunStatus.AWAITING_CONFIRM, resolved_url=plan["url"])
    if not await await_url_confirmation(agent_run_id):
        await _finish(
            agent_run_id, succeeded=False,
            reason="user did not confirm the resolved URL", tokens=0,
        )
        return

    # --- Create the workflow the recording session will populate ---
    async with async_session() as db:
        workflow = Workflow(
            user_id=user_id,
            name=(plan.get("summary") or prompt)[:200],
            start_url=plan["url"],
            status=WorkflowStatus.RECORDING,
            agent_run_id=agent_run_id,
        )
        db.add(workflow)
        await db.commit()
        await db.refresh(workflow)

        run = await db.get(AgentRun, agent_run_id)
        run.workflow_id = workflow.id
        await db.commit()

    # --- Drive / distill / extract / verify, repairing on failure ---
    total_tokens = 0
    last_verify: VerifyResult | None = None
    hint = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if time.monotonic() - started > WALL_CLOCK_SECONDS:
            await _finish(
                agent_run_id, succeeded=False, reason="exceeded the time budget",
                tokens=total_tokens,
            )
            return

        status = AgentRunStatus.DRIVING if attempt == 1 else AgentRunStatus.REPAIRING
        await _set_status(agent_run_id, status, attempt=attempt)

        outcome: dict = {"marks": [], "tokens": 0, "gave_up_reason": None, "blocked": False}

        async def _on_progress(name, arguments, text, _run_id=agent_run_id) -> None:
            await publish(_run_id, {"t": "step", "tool": name, "detail": text})

        async def agent_driver(session, _hint=hint, _outcome=outcome) -> None:
            result = await drive(session.page, plan, on_progress=_on_progress, hint=_hint)
            _outcome["marks"] = result.marks
            _outcome["tokens"] = result.tokens
            if result.gave_up:
                _outcome["gave_up_reason"] = result.give_up_reason
                _outcome["blocked"] = result.blocked
                return
            if result.marks:
                try:
                    session.extraction = await build_extraction(session.page, result.marks, plan)
                except ExtractionError as exc:
                    _outcome["gave_up_reason"] = f"extraction failed: {exc}"

        session = RecordingSession(
            str(workflow.id), str(user_id), headless=True, agent_driver=agent_driver,
        )
        await session.run()
        total_tokens += outcome["tokens"]

        if outcome["gave_up_reason"]:
            reason = outcome["gave_up_reason"]
            log.info("agent %s attempt %s gave up: %s", agent_run_id, attempt, reason)
            # A bot wall is not a strategy error, so the repair loop has nothing
            # to change — every further attempt would fail identically at the
            # first navigation, on the user's money.
            if outcome["blocked"] or attempt >= MAX_ATTEMPTS:
                await _finish(agent_run_id, succeeded=False, reason=reason, tokens=total_tokens)
                return
            # Feeds the next attempt a redacted transcript, not just the
            # reason — build_repair_context always routes through
            # redact_steps, since this is the one path where recorded
            # credentials would otherwise reach the model.
            hint = build_repair_context(session.steps, reason)
            continue

        bad_sample = sample_failure_reason(session.final_sample, plan.get("fields"))
        if bad_sample:
            log.info("agent %s attempt %s bad sample: %s", agent_run_id, attempt, bad_sample)
            if attempt >= MAX_ATTEMPTS:
                await _finish(
                    agent_run_id, succeeded=False, reason=bad_sample, tokens=total_tokens,
                )
                return
            hint = build_repair_context(
                session.steps, f"{bad_sample}\n{_STRATEGY_HINTS['fields_present']}"
            )
            continue

        try:
            bound_steps = bind_parameters(session.steps, plan)
            bound_steps = append_extract_step(bound_steps, session.extraction)
        except DistillError as exc:
            reason = str(exc)
            log.info("agent %s attempt %s distill failed: %s", agent_run_id, attempt, reason)
            if attempt >= MAX_ATTEMPTS:
                await _finish(agent_run_id, succeeded=False, reason=reason, tokens=total_tokens)
                return
            hint = build_repair_context(session.steps, reason)
            continue

        snapshot = {"steps": bound_steps, "extraction": session.extraction}
        await _set_status(agent_run_id, AgentRunStatus.VERIFYING, attempt=attempt)
        verify_result = await verify_workflow(
            snapshot, plan, session.final_sample, workflow_id=workflow.id,
        )
        last_verify = verify_result
        await publish(agent_run_id, {
            "t": "verify",
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in verify_result.checks
            ],
        })

        if verify_result.passed:
            async with async_session() as db:
                wf = await db.get(Workflow, workflow.id)
                wf.steps = bound_steps
                wf.extraction = session.extraction
                wf.parameters = [
                    {"name": p["name"], "type": p["type"], "required": p["required"],
                     "description": p.get("description")}
                    for p in plan["parameters"]
                ]
                wf.status = WorkflowStatus.READY
                await db.commit()

                run = await db.get(AgentRun, agent_run_id)
                run.attempt = attempt
                await db.commit()

            await _finish(agent_run_id, succeeded=True, reason=None, tokens=total_tokens)
            await publish(agent_run_id, {"t": "workflow_ready", "workflow_id": str(workflow.id)})
            return

        verify_hint = repair_hint(verify_result)
        log.info("agent %s attempt %s failed verify: %s", agent_run_id, attempt, verify_hint)
        # Redacted transcript + which checks failed, not just the failure
        # text: see the gave_up_reason branch above for why this always
        # routes through build_repair_context.
        hint = build_repair_context(bound_steps, verify_hint)
        if not should_retry(verify_result, attempt):
            break

    failure = last_verify.failure_summary() if last_verify else "verification never ran"
    async with async_session() as db:
        run = await db.get(AgentRun, agent_run_id)
        run.attempt = MAX_ATTEMPTS
        await db.commit()
    await _finish(agent_run_id, succeeded=False, reason=failure, tokens=total_tokens)
