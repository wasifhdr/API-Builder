from unittest.mock import AsyncMock, patch

import pytest

from app.agent.driver import drive
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


@pytest.mark.asyncio
async def test_drive_runs_tool_calls_until_done(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [
        _turn(ToolCall("1", "navigate", {"url": f"{fixture_site_url}/search.html?q=television"})),
        _turn(ToolCall("2", "mark_target", {"ref": "ref_0"})),
        _turn(ToolCall("3", "done", {})),
    ]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.marks == ["ref_0"]
    assert not result.gave_up
    assert result.turns == 3
    assert result.tokens == 30


@pytest.mark.asyncio
async def test_drive_records_give_up(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(ToolCall("1", "give_up", {"reason": "login wall"}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.gave_up
    assert "login wall" in result.give_up_reason


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
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(text="I am thinking"), _turn(ToolCall("1", "done", {}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan, max_turns=5)

    assert result.turns == 2


@pytest.mark.asyncio
async def test_drive_calls_on_progress_for_each_tool_call(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(ToolCall("1", "done", {}))]
    seen = []

    async def _on_progress(name, arguments, text):
        seen.append(name)

    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        await drive(fixture_page, plan, on_progress=_on_progress)

    assert seen == ["done"]
