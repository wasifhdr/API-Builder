import copy
import re
from urllib.parse import quote

SECRET_SELECTOR_RE = re.compile(r"password|passwd|pwd|otp|pin|cvv|secret", re.IGNORECASE)
MAX_LITERAL_CHARS = 120
REDACTED = "[REDACTED]"

VALUE_STEP_TYPES = {"fill", "select_option"}


class DistillError(Exception):
    """The transcript could not be turned into a usable workflow."""


def redact_steps(steps: list[dict]) -> list[dict]:
    """Strips credentials before any step list is shown to the model.

    injected.js does no input-type filtering, so a typed password lands in the
    steps as a plain literal. Returns a copy; never mutates the caller's list.
    """
    safe = copy.deepcopy(steps)
    for step in safe:
        value = step.get("value")
        if not isinstance(value, dict) or "literal" not in value:
            continue
        selectors = " ".join(step.get("selectors") or [])
        if SECRET_SELECTOR_RE.search(selectors):
            value["literal"] = REDACTED
        else:
            value["literal"] = str(value["literal"])[:MAX_LITERAL_CHARS]
    return safe


def quote_plus_safe(value: str) -> str:
    return quote(value, safe="").replace("%20", "+")


def _template_url(url: str, name: str, drive_value: str) -> str | None:
    """Replaces a drive value inside a URL with a {param} placeholder, trying
    the encoded form as well since the browser will have encoded it."""
    for candidate in (drive_value, quote(drive_value, safe=""), quote_plus_safe(drive_value)):
        if candidate and candidate in url:
            return url.replace(candidate, "{" + name + "}")
    return None


def bind_parameters(steps: list[dict], plan: dict) -> list[dict]:
    """Turns the agent's literal drive values into parameter references.

    This is a string match, not an inference: the plan chose drive_value before
    the browser opened, so the step that consumed it is identifiable exactly.
    Covers both step values and goto URLs — the agent can reach results either
    by typing or by navigating, and a URL-bound parameter left literal would
    silently produce an API that ignores its own input.

    Returns a new step list; never mutates the caller's.
    """
    bound = copy.deepcopy(steps)

    for parameter in plan.get("parameters") or []:
        name = parameter["name"]
        drive_value = parameter["drive_value"]
        used = False

        for step in bound:
            stype = step.get("type")

            if stype in VALUE_STEP_TYPES:
                value = step.get("value")
                if isinstance(value, dict) and value.get("literal") == drive_value:
                    step["value"] = {"param": name}
                    used = True

            elif stype == "goto":
                template = _template_url(step.get("url", ""), name, drive_value)
                if template is not None:
                    # Keep the literal url for display; replay prefers the template.
                    step["url_template"] = template
                    used = True

        if not used:
            raise DistillError(
                f"parameter {name!r} was never used during the run — "
                f"the value {drive_value!r} appears in no step"
            )

    return bound


def append_extract_step(steps: list[dict], extraction: dict) -> list[dict]:
    """Appends the terminal extract step, matching what RecordingSession does
    at save time. Returns a new list."""
    result = copy.deepcopy(steps)
    if extraction.get("main") and not any(s.get("type") == "extract" for s in result):
        result.append({"type": "extract", "ref": "main"})
    return result
