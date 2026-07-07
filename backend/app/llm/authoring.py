import logging
import re

from app.llm.client import complete_json
from app.llm.prompts import (
    build_extraction_naming_prompt,
    build_extraction_naming_schema,
    build_parameter_suggestion_prompt,
    build_parameter_suggestion_schema,
)
from app.recorder.constants import VALUE_STEP_TYPES

log = logging.getLogger("llm.authoring")

# Steps whose selectors match this are dropped before ever reaching the model —
# recorded literals can be credentials if the user logs in mid-recording, and
# the recorder's injected.js does no input-type filtering.
_REDACT_RE = re.compile(r"password|passwd|pwd|otp|pin|cvv|secret", re.IGNORECASE)
_MAX_LITERAL_CHARS = 120

_VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}
_VALID_TAKE = {"text", "html", "attr:href", "attr:src"}
_VALID_TRANSFORM = {"none", "trim", "number", "abs_url"}
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_MAX_ATTEMPTS = 2  # initial attempt + one retry, matching enrich.py's shape


def _truncate_literal(value: object) -> str:
    text = str(value)
    if len(text) > _MAX_LITERAL_CHARS:
        return text[:_MAX_LITERAL_CHARS] + "…"
    return text


def _is_redacted(step: dict) -> bool:
    haystack = " ".join(step.get("selectors") or [])
    return bool(_REDACT_RE.search(haystack))


def _candidate_steps(steps: list[dict]) -> list[dict]:
    """Steps `mark_param` can actually convert: unmarked fill/select_option
    steps still holding a literal, minus anything that looks like a
    credential. Returns a minimal, already-redacted/truncated view — never the
    raw step dicts — so nothing sensitive or oversized reaches the prompt."""
    candidates = []
    for step in steps:
        if step.get("type") not in VALUE_STEP_TYPES:
            continue
        value = step.get("value")
        if not isinstance(value, dict) or "literal" not in value:
            continue
        if _is_redacted(step):
            continue
        selectors = step.get("selectors") or []
        candidates.append({
            "i": step["i"],
            "selector": selectors[0] if selectors else None,
            "literal": _truncate_literal(value["literal"]),
        })
    return candidates


def _validate_parameter_suggestions(result: dict, candidate_indices: set[int]) -> list[dict]:
    raw = result.get("parameters")
    if not isinstance(raw, list):
        raise ValueError("response missing a 'parameters' list")

    suggestions = []
    seen_steps: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        step_i = item.get("step_i")
        name = item.get("name")
        if not isinstance(step_i, int) or step_i not in candidate_indices or step_i in seen_steps:
            continue
        if not isinstance(name, str) or not _NAME_RE.match(name):
            continue

        ptype = item.get("type")
        if ptype not in _VALID_PARAM_TYPES:
            ptype = "string"

        description = item.get("description")
        if not isinstance(description, str):
            description = None

        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None

        seen_steps.add(step_i)
        suggestions.append({
            "step_i": step_i,
            "name": name,
            "type": ptype,
            "example": item.get("example") if isinstance(item.get("example"), str) else None,
            "description": description,
            "confidence": confidence,
        })
    return suggestions


async def suggest_parameters(steps: list[dict]) -> list[dict]:
    """Suggests which recorded fill/select_option steps should become API
    parameters, with a proposed name/type/example/description. Advisory only —
    callers must route acceptance through the existing mark_param command.
    Raises after exhausting retries so callers can decide how to surface the
    failure (same shape as app.llm.enrich.enrich_spec)."""
    candidates = _candidate_steps(steps)
    if not candidates:
        return []

    schema = build_parameter_suggestion_schema()
    system, user = build_parameter_suggestion_prompt(steps, candidates)
    candidate_indices = {c["i"] for c in candidates}

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        prompt = user if attempt == 0 else f"{user}\n\nThe previous attempt was invalid: {last_error}. Fix it."
        try:
            result = await complete_json(system, prompt, schema)
            return _validate_parameter_suggestions(result, candidate_indices)
        except Exception as exc:
            last_error = exc
            log.warning("parameter suggestion attempt %s failed: %s", attempt, exc)

    raise RuntimeError(f"parameter suggestion failed after retry: {last_error}")


def _validate_extraction_suggestions(result: dict, selectors: list[str]) -> list[dict]:
    raw = result.get("fields")
    if not isinstance(raw, list):
        raise ValueError("response missing a 'fields' list")

    valid_selectors = set(selectors)
    suggestions = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        selector = item.get("selector")
        name = item.get("name")
        if not isinstance(selector, str) or selector not in valid_selectors or selector in seen:
            continue
        if not isinstance(name, str) or not _NAME_RE.match(name):
            continue

        take = item.get("take")
        if take not in _VALID_TAKE:
            continue  # can't safely default a take mode — drop the suggestion

        transform = item.get("transform")
        if transform not in _VALID_TRANSFORM:
            transform = "none"

        seen.add(selector)
        suggestions.append({"selector": selector, "name": name, "take": take, "transform": transform})
    return suggestions


async def suggest_extraction_fields(config: dict, sample: object) -> list[dict]:
    """Suggests field names/take/transform for the current extraction config,
    given a freshly-run sample row. Advisory only — callers must route
    acceptance through the existing set_extraction command. Raises after
    exhausting retries so callers can decide how to surface the failure."""
    fields = config.get("fields") or []
    if not fields:
        return []

    schema = build_extraction_naming_schema()
    system, user = build_extraction_naming_prompt(config, sample)
    selectors = [f.get("selector") for f in fields if f.get("selector")]

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        prompt = user if attempt == 0 else f"{user}\n\nThe previous attempt was invalid: {last_error}. Fix it."
        try:
            result = await complete_json(system, prompt, schema)
            return _validate_extraction_suggestions(result, selectors)
        except Exception as exc:
            last_error = exc
            log.warning("extraction naming attempt %s failed: %s", attempt, exc)

    raise RuntimeError(f"extraction naming failed after retry: {last_error}")
