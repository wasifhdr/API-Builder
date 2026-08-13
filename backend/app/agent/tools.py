import logging
from dataclasses import dataclass

from playwright.async_api import Page

from app.agent.observe import Observation, RefNotFound, observe, resolve_ref

log = logging.getLogger("agent")

# MUST stay above injected.js's 400ms fill debounce. Verified empirically:
# `fill` is emitted on a 400ms timer while `click` emits synchronously, so an
# agent that fills and immediately clicks gets the two steps recorded in the
# WRONG ORDER — replay would then click Search before typing the query. A human
# never types and clicks inside 400ms; an agent does it every time. Do not
# lower this without re-running the ordering test in
# tests/test_agent_recorder_capture.py.
POST_ACTION_SETTLE_MS = 1200


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _fn("navigate", "Load a URL. Prefer interacting with the page over "
        "navigating directly to a result URL — a hardcoded result URL "
        "produces an API that ignores its own parameters.",
        {"url": {"type": "string"}}, ["url"]),
    _fn("click", "Click the element with the given ref from the latest observation.",
        {"ref": {"type": "string"}}, ["ref"]),
    _fn("fill", "Type a value into the input with the given ref.",
        {"ref": {"type": "string"}, "value": {"type": "string"}}, ["ref", "value"]),
    _fn("press", "Press a keyboard key (for example Enter) on the element with the given ref.",
        {"ref": {"type": "string"}, "key": {"type": "string"}}, ["ref", "key"]),
    _fn("scroll", "Scroll the page to load lazily-rendered content.",
        {"direction": {"type": "string", "enum": ["down", "up"]}}, ["direction"]),
    _fn("mark_target", "Mark the element with the given ref as part of the data "
        "the API should extract. First call: the repeating container/row "
        "(one representative row, not every row). Then one more call per "
        "requested field, on that field's own element within the row.",
        {"ref": {"type": "string"}}, ["ref"]),
    _fn("done", "The workflow is complete and the target data is on screen.", {}, []),
    _fn("give_up", "Stop: this task cannot be completed (for example a login wall).",
        {"reason": {"type": "string"}}, ["reason"]),
]


@dataclass
class ToolOutcome:
    text: str
    observation: Observation | None = None
    finished: bool = False
    gave_up: bool = False


async def dispatch(page: Page, name: str, arguments: dict, marks: list[str]) -> ToolOutcome:
    """Executes one agent tool call against the live page.

    Never raises: a bad ref, a missing element, or a navigation error is
    reported back to the model as text so it can correct itself. Killing the
    run on a recoverable mistake would waste the whole authoring attempt.
    """
    if name == "done":
        return ToolOutcome(text="done", finished=True)
    if name == "give_up":
        reason = arguments.get("reason") or "no reason given"
        return ToolOutcome(text=f"gave up: {reason}", finished=True, gave_up=True)

    try:
        if name == "navigate":
            await page.goto(arguments["url"], wait_until="domcontentloaded")
        elif name == "scroll":
            delta = 2000 if arguments.get("direction", "down") == "down" else -2000
            await page.mouse.wheel(0, delta)
        elif name in {"click", "fill", "press"}:
            element = await resolve_ref(page, arguments.get("ref", ""))
            if name == "click":
                await element.click()
            elif name == "fill":
                await element.fill(str(arguments.get("value", "")))
            else:
                await element.press(arguments.get("key", "Enter"))
        elif name == "mark_target":
            ref = arguments.get("ref", "")
            await resolve_ref(page, ref)  # validates it exists
            marks.append(ref)
            return ToolOutcome(text=f"marked {ref} as an extraction target")
        else:
            return ToolOutcome(text=f"unknown tool {name!r}")
    except RefNotFound as exc:
        return ToolOutcome(text=str(exc))
    except Exception as exc:
        log.info("agent tool %s failed: %s", name, exc)
        return ToolOutcome(text=f"{name} failed: {exc}")

    await page.wait_for_timeout(POST_ACTION_SETTLE_MS)
    observation = await observe(page)
    return ToolOutcome(text=f"{name} ok", observation=observation)
