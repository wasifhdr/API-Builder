import json
import logging
import uuid
from dataclasses import dataclass, field

from app.recorder.replay import ReplayError, replay_workflow

log = logging.getLogger("agent")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class VerifyResult:
    checks: list[CheckResult] = field(default_factory=list)
    data: object = None

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def failure_summary(self) -> str:
        return "; ".join(c.detail for c in self.checks if not c.passed)


def _rows(data: object) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, default=str)


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def missing_field_names(data: object, fields: list[dict] | None) -> list[str]:
    """Declared fields that carry no usable value anywhere in `data`.

    Key presence is not evidence. Every row dict the extractor builds carries
    every declared key, so `name in row` is satisfied by a row of pure nulls —
    which is how a workflow returning nothing for every field reached READY and
    got published. A blank string counts as missing for the same reason: an
    empty text node is not extracted data.

    A field populated in only ONE row is present. Sparse fields (a discount
    price on 3 of 20 rows) are normal data, so no fill-rate threshold applies.
    """
    declared = [f["name"] for f in fields or []]
    rows = _rows(data)
    if not rows:
        return declared
    return [n for n in declared if all(_is_empty(row.get(n)) for row in rows)]


async def verify_workflow(
    snapshot: dict,
    plan: dict,
    drive_data: object,
    *,
    workflow_id: uuid.UUID | None = None,
) -> VerifyResult:
    """Replays the distilled workflow with values the agent never drove with.

    The differential check is the one that matters: an agent that navigated to
    a literal result URL produces a workflow that passes every structural check
    and still returns identical data for every input. Nothing else catches it.
    """
    params = {p["name"]: p["verify_value"] for p in plan.get("parameters") or []}
    result = VerifyResult()

    try:
        replay = await replay_workflow(
            snapshot, params, None, uuid.uuid4(), headless=True, workflow_id=workflow_id
        )
    except ReplayError as exc:
        result.checks.append(CheckResult("replays", False, f"replay failed: {exc}"))
        return result
    except Exception as exc:
        result.checks.append(CheckResult("replays", False, f"replay errored: {exc}"))
        return result

    data = replay.get("data")
    result.data = data
    result.checks.append(CheckResult("replays", True, "replay completed"))

    rows = _rows(data)
    # A stricter minimum is not imposed: a legitimate search can return a
    # single result, and a zero-row result is indistinguishable from a
    # broken extraction.
    has_rows = len(rows) >= 1
    result.checks.append(CheckResult(
        "has_rows", has_rows,
        "extraction returned no rows" if not has_rows else f"{len(rows)} row(s)",
    ))

    missing = missing_field_names(data, plan.get("fields"))
    result.checks.append(CheckResult(
        "fields_present", not missing,
        f"declared fields missing from output: {', '.join(missing)}" if missing
        else "all declared fields present",
    ))

    differs = _canonical(data) != _canonical(drive_data)
    result.checks.append(CheckResult(
        "differs_from_drive", differs,
        "output is identical for a different parameter value — the workflow "
        "ignores its own parameter (likely a hardcoded URL)" if not differs
        else "output changed with the parameter",
    ))

    return result
