import json
import logging
from dataclasses import dataclass, field

from playwright.async_api import Page

from app.agent.observe import observe
from app.agent.tools import TOOL_SCHEMAS, dispatch
from app.recorder import blocked
from app.llm.client import complete_tools, tool_result_message, user_message

log = logging.getLogger("agent")

DRIVE_SYSTEM = (
    "You are building a reusable web API by driving a real browser.\n\n"
    "Your job: perform the described task once, using the example values given, "
    "then mark the data the API should return and call done.\n\n"
    "Critical rules:\n"
    "- Reach results by INTERACTING with the page (type into the search box, "
    "click the button). Do NOT navigate straight to a result URL you guessed — "
    "that produces an API that returns the same data for every input, which "
    "fails verification.\n"
    "- Use the exact example value given for each parameter, so the value can "
    "be recognised and turned into a parameter afterwards.\n"
    "- Marking data to extract is a TWO-STEP sequence, always in this order: "
    "(1) call mark_target ONCE on a repeating container — one representative "
    "product/result row, not every row. (2) call mark_target ONCE MORE for "
    "EACH requested data field, in the order they were listed, on the specific "
    "element holding that field's value inside that same row (e.g. the title "
    "text itself, then the price text itself) — never mark the row again for "
    "these. Observations list these field elements underneath their row, "
    "labelled 'field inside ref_N'. If you cannot find a ref for some field, "
    "skip marking it rather than reusing the row's or another field's ref.\n"
    "- Refs come from the most recent observation only. After any navigation, "
    "the previous refs are void.\n"
    "- If the task needs a login, call give_up — you must never enter credentials."
)


@dataclass
class DriveResult:
    marks: list[str] = field(default_factory=list)
    gave_up: bool = False
    give_up_reason: str | None = None
    turns: int = 0
    tokens: int = 0
    # True when the run ended because the site refused automated visits. The
    # runner reads this to skip its repair attempts: no change of strategy
    # gets past a wall, so retrying only spends the user's money twice more.
    blocked: bool = False


def _task_brief(plan: dict) -> str:
    params = "\n".join(
        f"- {p['name']} ({p['type']}): use the value {p['drive_value']!r}"
        for p in plan["parameters"]
    )
    fields = ", ".join(f["name"] for f in plan["fields"])
    return (
        f"Task: {plan.get('summary') or 'build the described API'}\n"
        f"You are already at the start URL ({plan['url']}) — the observation "
        "below shows the current page. Do not navigate there again.\n"
        f"Parameters to exercise:\n{params or '- (none)'}\n"
        f"Data fields the API must return: {fields}"
    )


async def drive(page: Page, plan: dict, max_turns: int = 25, on_progress=None) -> DriveResult:
    """Runs the agent's tool-calling loop against a live page.

    The page belongs to a live RecordingSession, so every action taken here is
    captured as a workflow step with ranked selectors by the injected recorder —
    this function never builds a selector itself.
    """
    result = DriveResult()
    marks: list[str] = result.marks

    # Pre-flight, before the first (billable) model call: a block page is still
    # a page, so without this the model would spend its whole turn budget
    # hunting for a search box on a 689-character "you have been blocked"
    # document and then give up for an unrelated-sounding reason.
    wall = await blocked.detect(page)
    if wall is not None:
        result.gave_up = True
        result.blocked = True
        result.give_up_reason = wall.message()
        if on_progress is not None:
            await on_progress("blocked", {}, wall.message())
        return result

    observation = await observe(page)
    messages: list[dict] = [
        user_message(f"{_task_brief(plan)}\n\n{observation.tree}", observation.screenshot_b64)
    ]

    for _ in range(max_turns):
        turn = await complete_tools(DRIVE_SYSTEM, messages, TOOL_SCHEMAS)
        result.turns += 1
        result.tokens += turn.usage_tokens

        if not turn.tool_calls:
            # A text-only turn is the model thinking out loud; nudge it back to
            # acting rather than ending the run.
            messages.append({"role": "assistant", "content": turn.text or ""})
            messages.append(user_message("Call a tool to continue, or call give_up."))
            continue

        messages.append({
            "role": "assistant",
            "content": turn.text,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                 # Echoes back provider-specific data (e.g. Gemini's
                 # thought_signature) attached to this call — omitting it is
                 # accepted for a while and then rejected once enough turns
                 # accumulate (openai.BadRequestError, "missing a
                 # thought_signature"), so a short conversation never catches
                 # a missing echo here.
                 **({"extra_content": c.raw_extra} if c.raw_extra else {})}
                for c in turn.tool_calls
            ],
        })

        finished = False
        for call in turn.tool_calls:
            outcome = await dispatch(page, call.name, call.arguments, marks)
            if on_progress is not None:
                await on_progress(call.name, call.arguments, outcome.text)

            messages.append(tool_result_message(call.id, outcome.text))

            if outcome.gave_up:
                result.gave_up = True
                result.give_up_reason = outcome.text
                result.blocked = result.blocked or outcome.blocked
            if outcome.finished:
                finished = True
                break
            if outcome.observation is not None:
                messages.append(
                    user_message(outcome.observation.tree, outcome.observation.screenshot_b64)
                )

        if finished:
            break

    return result
