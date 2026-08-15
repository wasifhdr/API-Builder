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
    "- Extract at the altitude the task states. For a list task the answer is "
    "the repeating rows on the page your interaction produced; clicking into "
    "one of them produces an API that returns that one record for every "
    "input.\n"
    "- Use the exact example value given for each parameter, so the value can "
    "be recognised and turned into a parameter afterwards.\n"
    "- Marking data to extract is a TWO-STEP sequence, always in this order: "
    "(1) call mark_target ONCE on a repeating container — one representative "
    "product/result row, not every row — with no 'field' argument. (2) call "
    "mark_target ONCE MORE for EACH requested data field, passing "
    "field=<the exact declared field name>, on the specific element holding "
    "that field's value inside that same row (e.g. the title text itself, "
    "then the price text itself) — never mark the row again for these. "
    "Observations list these field elements underneath their row, labelled "
    "'field inside ref_N'.\n"
    "- For a field whose value is a URL rather than visible text, mark the "
    "<a> and pass take='attr:href'; for an image field mark the <img> and pass "
    "take='attr:src'. Marking a link and leaving take as text yields the link "
    "LABEL, not the URL.\n"
    "- done is REJECTED while any requested field is unmarked. If no element "
    "holds a field's value exactly, mark the closest element that CONTAINS it "
    "rather than abandoning the task — partial data beats no API. Imperfect "
    "or inconsistent results (mixed-in items, missing prices on some rows) are "
    "normal web pages, not a reason to stop.\n"
    "- Refs come from the most recent observation only. After any navigation, "
    "the previous refs are void.\n"
    "- If the task needs a login, call give_up — you must never enter credentials."
)


# How many times `done` may be bounced back for unmarked fields before it is
# honoured anyway. Without a ceiling a field that genuinely is not on the page
# (the model is right, the plan is wrong) would burn every remaining turn; with
# it, the run still finishes and the missing field surfaces at verify instead.
MAX_DONE_REFUSALS = 2


@dataclass
class DriveResult:
    marks: list[dict] = field(default_factory=list)
    gave_up: bool = False
    give_up_reason: str | None = None
    turns: int = 0
    tokens: int = 0
    # True when the run ended because the site refused automated visits. The
    # runner reads this to skip its repair attempts: no change of strategy
    # gets past a wall, so retrying only spends the user's money twice more.
    blocked: bool = False
    # True when the user stopped the run mid-drive. Distinct from gave_up:
    # there is no failure to repair or report, so the runner ends the run
    # outright instead of feeding this into another attempt.
    cancelled: bool = False


_SHAPE_RULES = {
    "list": (
        "The data lives on the page your interaction produces. Mark a "
        "representative result row THERE. Do NOT open an individual result — "
        "an API built from one record's page returns that one record forever."
    ),
    "detail": (
        "Reach the single record the request describes, then mark its fields."
    ),
}


def _task_brief(plan: dict, hint: str = "") -> str:
    params = "\n".join(
        f"- {p['name']} ({p['type']}): use the value {p['drive_value']!r}"
        for p in plan["parameters"]
    )
    fields = ", ".join(f["name"] for f in plan["fields"])
    shape = plan.get("result_shape")
    if shape not in _SHAPE_RULES:
        shape = "list"
    sections = [
        f"Task: {plan.get('summary') or 'build the described API'}",
        f"You are already at the start URL ({plan['url']}) — the observation "
        "below shows the current page. Do not navigate there again.",
        _SHAPE_RULES[shape],
        f"Parameters to exercise:\n{params or '- (none)'}",
        f"Data fields the API must return: {fields}",
    ]
    if hint:
        # Its own labelled section: appending this to the task statement made
        # the model read a failure transcript as its goal.
        sections.append(f"PREVIOUS ATTEMPT (do not repeat this route):\n{hint}")
    return "\n".join(sections)


async def drive(
    page: Page, plan: dict, max_turns: int = 25, on_progress=None, hint: str = "",
    should_stop=None,
) -> DriveResult:
    """Runs the agent's tool-calling loop against a live page.

    The page belongs to a live RecordingSession, so every action taken here is
    captured as a workflow step with ranked selectors by the injected recorder —
    this function never builds a selector itself.

    `should_stop` is an async predicate polled once per turn, before the
    (billable) model call. A turn is the finest granularity a stop can take
    effect at without abandoning an in-flight browser action mid-way, and it is
    the boundary that matters for cost: the user is never charged for a turn
    requested after they hit stop.
    """
    result = DriveResult()
    marks: list[dict] = result.marks
    done_refusals = 0

    if should_stop is not None and await should_stop():
        result.cancelled = True
        return result

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
        user_message(
            f"{_task_brief(plan, hint)}\n\n{observation.tree}", observation.screenshot_b64
        )
    ]

    for _ in range(max_turns):
        if should_stop is not None and await should_stop():
            result.cancelled = True
            return result

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
            outcome = await dispatch(
                page, call.name, call.arguments, marks,
                fields=plan.get("fields"),
                enforce_marks=done_refusals < MAX_DONE_REFUSALS,
            )
            if call.name == "done" and not outcome.finished:
                done_refusals += 1
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
