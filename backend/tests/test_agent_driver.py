from unittest.mock import AsyncMock, patch

import pytest

from app.agent.driver import MAX_DONE_REFUSALS, _task_brief, drive
from app.llm.client import ToolCall, TurnResult


def _turn(*calls, text=None, tokens=10):
    return TurnResult(tool_calls=list(calls), text=text, usage_tokens=tokens)


PLAN = {
    "url": "http://placeholder/search.html",
    "parameters": [{"name": "query", "type": "string", "required": True,
                    "drive_value": "television", "verify_value": "fan",
                    "description": None}],
    "fields": [{"name": "title", "type": "string"}],
}

# `done` is gated on every declared field being marked. These loop-mechanics
# tests are about turn handling, not the marking contract (which
# tests/test_agent_tools.py covers at the dispatch level), so they declare no
# fields and leave the gate a no-op rather than restating it in each one.
NO_FIELD_PLAN = {**PLAN, "fields": []}


@pytest.mark.asyncio
async def test_drive_runs_tool_calls_until_done(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [
        _turn(ToolCall("1", "navigate", {"url": f"{fixture_site_url}/search.html?q=television"})),
        _turn(ToolCall("2", "mark_target", {"ref": "ref_0"})),
        # Same ref for the field mark: which element it is does not matter to
        # the loop, and reusing ref_0 keeps the test off the ref numbering the
        # fixture page happens to produce.
        _turn(ToolCall("3", "mark_target", {"ref": "ref_0", "field": "title"})),
        _turn(ToolCall("4", "done", {})),
    ]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.marks == [{"ref": "ref_0"}, {"ref": "ref_0", "field": "title"}]
    assert not result.gave_up
    assert result.turns == 4
    assert result.tokens == 40


@pytest.mark.asyncio
async def test_drive_refuses_done_until_every_field_is_marked(fixture_site_url, fixture_page):
    """Calling done with only the row marked used to end the drive with no
    field selectors at all, which surfaced to the user as an API returning
    nulls. The refusal is what gives the model a chance to correct itself."""
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [
        _turn(ToolCall("1", "mark_target", {"ref": "ref_0"})),
        _turn(ToolCall("2", "done", {})),  # refused - title is unmarked
        _turn(ToolCall("3", "mark_target", {"ref": "ref_0", "field": "title"})),
        _turn(ToolCall("4", "done", {})),  # honoured
    ]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan, max_turns=8)

    assert result.turns == 4
    assert [m.get("field") for m in result.marks] == [None, "title"]


@pytest.mark.asyncio
async def test_drive_honours_done_once_the_refusal_budget_is_spent(
    fixture_site_url, fixture_page
):
    """A field that genuinely is not on the page must not burn every remaining
    turn — after MAX_DONE_REFUSALS the drive ends and verify reports the gap."""
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [
        _turn(ToolCall("0", "mark_target", {"ref": "ref_0"})),
        *[_turn(ToolCall(str(i), "done", {})) for i in range(1, 5)],
    ]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan, max_turns=8)

    assert result.turns == MAX_DONE_REFUSALS + 2  # the row mark, then the dones
    assert not result.gave_up


@pytest.mark.asyncio
async def test_drive_never_finishes_with_nothing_marked(fixture_site_url, fixture_page):
    """Repeating `done` must not end the attempt empty once the refusal budget
    is spent — an unmarked drive cannot produce an API, so it runs the turns
    out instead."""
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    endless = _turn(ToolCall("x", "done", {}))
    with patch("app.agent.driver.complete_tools", AsyncMock(return_value=endless)):
        result = await drive(fixture_page, plan, max_turns=5)

    assert result.turns == 5, "done was honoured despite nothing being marked"
    assert result.marks == []


@pytest.mark.asyncio
async def test_drive_stops_before_spending_a_turn_when_cancelled(
    fixture_site_url, fixture_page
):
    """The stop check sits before the model call, not after it: a user who
    stops must not be billed for a turn requested after they clicked."""
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}

    async def stopped() -> bool:
        return True

    never = AsyncMock(side_effect=AssertionError("the model was called after a stop"))
    with patch("app.agent.driver.complete_tools", never):
        result = await drive(fixture_page, plan, should_stop=stopped)

    assert result.cancelled
    assert not result.gave_up
    assert result.turns == 0
    assert result.tokens == 0


@pytest.mark.asyncio
async def test_drive_stops_mid_loop_when_cancelled(fixture_site_url, fixture_page):
    """A stop arriving after the run is under way ends it at the next turn
    boundary, leaving the turns already taken accounted for."""
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    # Pre-flight, then turn one, then the stop lands before turn two.
    stops = iter([False, False, True])

    async def stopped() -> bool:
        return next(stops, True)

    turns = [_turn(ToolCall("1", "navigate", {"url": f"{fixture_site_url}/search.html?q=tv"}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan, should_stop=stopped)

    assert result.cancelled
    assert result.turns == 1
    assert result.tokens == 10


@pytest.mark.asyncio
async def test_drive_records_give_up(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(ToolCall("1", "give_up", {"reason": "login wall"}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.gave_up
    assert "login wall" in result.give_up_reason


@pytest.mark.asyncio
async def test_drive_ends_on_a_block_page_without_calling_the_model(
    fixture_site_url, fixture_page
):
    """The pre-flight check is what keeps a blocked site cheap: the model is
    never consulted, so the run costs nothing beyond the browser launch."""
    await fixture_page.goto(f"{fixture_site_url}/cf-blocked.html")
    plan = {**PLAN, "url": f"{fixture_site_url}/cf-blocked.html"}

    never = AsyncMock(side_effect=AssertionError("the model was called on a block page"))
    with patch("app.agent.driver.complete_tools", never):
        result = await drive(fixture_page, plan)

    assert result.gave_up
    assert result.blocked
    assert result.turns == 0
    assert result.tokens == 0
    assert "blocking automated visits" in result.give_up_reason


@pytest.mark.asyncio
async def test_drive_reports_a_block_hit_mid_run(fixture_site_url, fixture_page):
    """A site can also wall the agent partway through — after a search submits,
    say. That must end the run flagged as blocked, not merely as gave_up."""
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(ToolCall("1", "navigate", {"url": f"{fixture_site_url}/cf-blocked.html"}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.gave_up
    assert result.blocked
    assert result.turns == 1


@pytest.mark.asyncio
async def test_drive_stops_at_max_turns(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    endless = _turn(ToolCall("x", "scroll", {"direction": "down"}))
    with patch("app.agent.driver.complete_tools", AsyncMock(return_value=endless)):
        result = await drive(fixture_page, plan, max_turns=3)

    assert result.turns == 3
    assert not result.gave_up


@pytest.mark.asyncio
async def test_drive_treats_a_textonly_turn_as_a_nudge(fixture_site_url, fixture_page):
    plan = {**NO_FIELD_PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(text="I am thinking"), _turn(ToolCall("1", "done", {}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan, max_turns=5)

    assert result.turns == 2


@pytest.mark.asyncio
async def test_drive_echoes_provider_extra_content_on_the_next_turn(fixture_site_url, fixture_page):
    # Gemini rejects a multi-turn conversation once enough tool calls have
    # gone by without their thought_signature echoed back — a short mocked
    # conversation never hits that limit, so this checks the echo directly
    # rather than relying on the API to eventually reject a missing one.
    plan = {**NO_FIELD_PLAN, "url": f"{fixture_site_url}/search.html"}
    extra = {"google": {"thought_signature": "sig-1"}}
    turns = [
        _turn(ToolCall("1", "scroll", {"direction": "down"}, raw_extra=extra)),
        _turn(ToolCall("2", "done", {})),
    ]
    mock = AsyncMock(side_effect=turns)
    with patch("app.agent.driver.complete_tools", mock):
        await drive(fixture_page, plan, max_turns=5)

    second_call_messages = mock.call_args_list[1].args[1]
    assistant_message = next(m for m in second_call_messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistant_message["tool_calls"][0]["extra_content"] == extra


@pytest.mark.asyncio
async def test_drive_calls_on_progress_for_each_tool_call(fixture_site_url, fixture_page):
    plan = {**NO_FIELD_PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(ToolCall("1", "done", {}))]
    seen = []

    async def _on_progress(name, arguments, text):
        seen.append(name)

    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        await drive(fixture_page, plan, on_progress=_on_progress)

    assert seen == ["done"]


def _brief_plan(**overrides):
    plan = {
        "url": "https://example.com",
        "summary": "Search products",
        "result_shape": "list",
        "parameters": [{"name": "query", "type": "string", "drive_value": "refrigerator"}],
        "fields": [{"name": "title"}, {"name": "price"}],
    }
    plan.update(overrides)
    return plan


def test_task_brief_forbids_opening_a_result_for_a_list_shape():
    brief = _task_brief(_brief_plan())
    assert "Do NOT open an individual result" in brief


def test_task_brief_permits_reaching_one_record_for_a_detail_shape():
    brief = _task_brief(_brief_plan(result_shape="detail"))
    assert "Do NOT open an individual result" not in brief
    assert "single record" in brief


def test_task_brief_defaults_to_the_list_rule_when_the_shape_is_absent():
    plan = _brief_plan()
    del plan["result_shape"]
    assert "Do NOT open an individual result" in _task_brief(plan)


def test_a_repair_hint_is_labelled_and_not_folded_into_the_task():
    """The hint used to be appended to plan['summary'], so the model read a
    failure transcript where it expected a goal statement."""
    brief = _task_brief(_brief_plan(), hint="Previous attempt failed: nulls")
    task_line = brief.splitlines()[0]
    assert "Previous attempt failed" not in task_line
    assert "PREVIOUS ATTEMPT" in brief
    assert "Previous attempt failed: nulls" in brief


def test_no_hint_section_when_there_is_no_hint():
    assert "PREVIOUS ATTEMPT" not in _task_brief(_brief_plan())
