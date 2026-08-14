from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.client import (
    ToolCall,
    complete_tools,
    tool_result_message,
    user_message,
)


def _fake_response(tool_calls=None, content=None, total_tokens=42):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(total_tokens=total_tokens),
        model_dump=lambda: {},
    )


def _fake_tool_call(call_id, name, arguments_json, extra_content=None):
    kwargs = {"id": call_id, "function": SimpleNamespace(name=name, arguments=arguments_json)}
    if extra_content is not None:
        kwargs["extra_content"] = extra_content
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_complete_tools_parses_tool_calls():
    resp = _fake_response(tool_calls=[_fake_tool_call("c1", "click", '{"ref": "ref_3"}')])
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [{"type": "function"}])

    assert result.tool_calls == [ToolCall(id="c1", name="click", arguments={"ref": "ref_3"})]
    assert result.usage_tokens == 42


@pytest.mark.asyncio
async def test_complete_tools_parses_fenced_arguments():
    # Some providers wrap tool arguments in a markdown fence; _extract_json must save us.
    resp = _fake_response(tool_calls=[_fake_tool_call("c2", "fill", '```json\n{"ref": "ref_1"}\n```')])
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [])

    assert result.tool_calls[0].arguments == {"ref": "ref_1"}


@pytest.mark.asyncio
async def test_complete_tools_returns_text_when_no_tool_calls():
    resp = _fake_response(content="all done")
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [])

    assert result.tool_calls == []
    assert result.text == "all done"


@pytest.mark.asyncio
async def test_complete_tools_raises_on_gateway_error():
    resp = SimpleNamespace(choices=[], model_dump=lambda: {"message": "blocked"})
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="blocked"):
            await complete_tools("sys", [], [])


def test_user_message_without_screenshot_is_plain_text():
    assert user_message("hello") == {"role": "user", "content": "hello"}


def test_user_message_with_screenshot_uses_content_parts():
    msg = user_message("look", screenshot_b64="QUJD")
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "look"}
    assert msg["content"][1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_tool_result_message_shape():
    assert tool_result_message("c1", "ok") == {
        "role": "tool", "tool_call_id": "c1", "content": "ok",
    }


@pytest.mark.asyncio
async def test_complete_tools_honors_the_model_override():
    resp = _fake_response(content="ok")
    create = AsyncMock(return_value=resp)
    with patch("app.llm.client.client.chat.completions.create", create):
        await complete_tools("sys", [], [], model="some-stronger-model")

    assert create.call_args.kwargs["model"] == "some-stronger-model"


@pytest.mark.asyncio
async def test_complete_tools_captures_provider_extra_content():
    # Gemini attaches extra_content.google.thought_signature to each tool
    # call; omitting it when the call is echoed back on a later turn is
    # accepted for a while and then rejected once enough turns accumulate.
    extra = {"google": {"thought_signature": "abc123"}}
    resp = _fake_response(tool_calls=[_fake_tool_call("c1", "click", '{"ref": "ref_1"}', extra_content=extra)])
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [])

    assert result.tool_calls[0].raw_extra == extra


@pytest.mark.asyncio
async def test_complete_tools_defaults_raw_extra_to_none_when_absent():
    resp = _fake_response(tool_calls=[_fake_tool_call("c1", "click", '{"ref": "ref_1"}')])
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [])

    assert result.tool_calls[0].raw_extra is None


@pytest.mark.asyncio
async def test_empty_content_with_a_tool_call_is_not_treated_as_a_reply():
    # Gemini returns content='' (not None) alongside tool_calls.
    resp = _fake_response(tool_calls=[_fake_tool_call("c1", "done", "{}")], content="")
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [], [])

    assert result.tool_calls[0].name == "done"
    assert result.tool_calls[0].arguments == {}
