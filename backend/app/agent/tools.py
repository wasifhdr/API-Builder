import logging
from dataclasses import dataclass

from playwright.async_api import Page

from app.agent.extract import assign_marks, normalize_marks
from app.agent.observe import Observation, RefNotFound, observe, resolve_ref
from app.recorder import blocked

log = logging.getLogger("agent")

# MUST stay above injected.js's 400ms fill debounce. Verified empirically:
# `fill` is emitted on a 400ms timer while `click` emits synchronously, so an
# agent that fills and immediately clicks gets the two steps recorded in the
# WRONG ORDER — replay would then click Search before typing the query. A human
# never types and clicks inside 400ms; an agent does it every time. Do not
# lower this without re-running the ordering test in
# tests/test_agent_recorder_capture.py.
POST_ACTION_SETTLE_MS = 1200

# Additional grace for the page to render what the action asked for. The settle
# above is sized for OUR fill debounce, not for a storefront's search returning
# results over the network. Capped, because a page with analytics beacons or an
# open websocket never goes idle and the turn must not stall on it.
NETWORK_IDLE_TIMEOUT_MS = 6000


async def settle(page: Page) -> None:
    """Waits for the page to stop changing before it is observed.

    Network-idle is the signal that a search's results actually arrived. The
    fixed settle stays as the floor (it is what keeps recorded step ORDER
    correct), and the idle wait is best-effort on top — timing out is normal
    and must not fail the tool call.
    """
    await page.wait_for_timeout(POST_ACTION_SETTLE_MS)
    try:
        await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - a chatty page never idles; observe anyway
        pass


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
        "(one representative row, not every row) — omit 'field' for it. Then "
        "one more call per requested field, on that field's own element within "
        "the row, passing 'field' with that field's exact declared name. Pass "
        "'take' to say what to read off the element: 'text' for visible text, "
        "'attr:href' for a link's URL, 'attr:src' for an image's URL. If you "
        "omit 'take' a sensible one is inferred from the element.",
        {"ref": {"type": "string"},
         "field": {"type": "string"},
         "take": {"type": "string", "enum": ["text", "html", "attr:href", "attr:src"]}},
        ["ref"]),
    _fn("done", "The workflow is complete and every requested field has been "
        "marked with mark_target. Rejected if any declared field is still "
        "unmarked.", {}, []),
    _fn("give_up", "Stop: this task cannot be completed (for example a login wall).",
        {"reason": {"type": "string"}}, ["reason"]),
]


@dataclass
class ToolOutcome:
    text: str
    observation: Observation | None = None
    finished: bool = False
    gave_up: bool = False
    blocked: bool = False


def unmarked_fields(marks: list, fields: list[dict] | None) -> list[str]:
    """Declared fields that no mark_target call covers yet.

    marks[0] is the row container, so only marks[1:] carry fields. The mapping
    itself is assign_marks', so this gate and the extraction builder can never
    disagree about which fields are covered.
    """
    declared = [f["name"] for f in fields or []]
    if not declared or not marks:
        return declared
    assigned = assign_marks(normalize_marks(marks)[1:], fields)
    return [name for name in declared if name not in assigned]


async def dispatch(
    page: Page, name: str, arguments: dict, marks: list,
    fields: list[dict] | None = None, enforce_marks: bool = True,
) -> ToolOutcome:
    """Executes one agent tool call against the live page.

    Never raises: a bad ref, a missing element, or a navigation error is
    reported back to the model as text so it can correct itself. Killing the
    run on a recoverable mistake would waste the whole authoring attempt.
    """
    if name == "done":
        # The refusal budget exists for a field that genuinely is not on the
        # page, so the drive can still finish. It must NOT extend to a drive
        # that marked NOTHING: that cannot produce an API at all, so spending
        # the remaining turns is strictly better than ending empty. Letting the
        # budget cover this had the model answer `done` three times in a row
        # and finish with no marks, burning a full attempt each time.
        missing = unmarked_fields(marks, fields) if (enforce_marks or not marks) else []
        if missing and not marks:
            # The single most expensive way a run used to fail: the model
            # finished the interaction, called done without marking anything,
            # and the runner reported "the marked elements produced no value
            # for: <every field>" — from an extraction that was never built.
            return ToolOutcome(text=(
                "not done: you have not marked any data yet. Call mark_target on the "
                "repeating result row first, then once per requested field "
                f"({', '.join(missing)}) with that field's name."
            ))
        if missing:
            return ToolOutcome(text=(
                f"not done: nothing is marked for {', '.join(missing)}. Call mark_target "
                "on the element holding each of those values, passing field=<name>. "
                "If no element holds one exactly, mark the closest element that "
                "contains it — a partially populated field is still useful, and "
                "give_up is only for login walls and blocked pages."
            ))
        return ToolOutcome(text="done", finished=True)
    if name == "give_up":
        reason = arguments.get("reason") or "no reason given"
        return ToolOutcome(text=f"gave up: {reason}", finished=True, gave_up=True)

    try:
        if name == "navigate":
            response = await page.goto(arguments["url"], wait_until="domcontentloaded")
            # Ends the run rather than reporting back to the model: no wording
            # of the next tool call gets past a bot wall, so further turns would
            # only spend tokens on a page that will never yield the data.
            wall = await blocked.detect(page, response)
            if wall is not None:
                return ToolOutcome(
                    text=f"gave up: {wall.message()}",
                    finished=True, gave_up=True, blocked=True,
                )
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
            if not marks and arguments.get("field"):
                # marks[0] is always consumed as the row container. Letting a
                # field-named mark land there would make the row the field's
                # own element AND leave that field unmarked.
                return ToolOutcome(text=(
                    "not marked: the first mark_target must be the repeating result row, "
                    "with no 'field' argument. Mark the row, then mark "
                    f"{arguments['field']} inside it."
                ))
            mark: dict = {"ref": ref}
            if arguments.get("field"):
                mark["field"] = str(arguments["field"])
            if arguments.get("take"):
                mark["take"] = str(arguments["take"])
            marks.append(mark)
            label = f" for {mark['field']}" if "field" in mark else " as the result row"
            return ToolOutcome(text=f"marked {ref}{label}")
        else:
            return ToolOutcome(text=f"unknown tool {name!r}")
    except RefNotFound as exc:
        return ToolOutcome(text=str(exc))
    except Exception as exc:
        log.info("agent tool %s failed: %s", name, exc)
        return ToolOutcome(text=f"{name} failed: {exc}")

    await settle(page)
    observation = await observe(page)
    return ToolOutcome(text=f"{name} ok", observation=observation)
