import httpx
import pytest
from openai import RateLimitError

import app.llm.client as client_module
from app.llm.client import _extract_json


def test_extract_json_bare():
    assert _extract_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_extract_json_code_fence():
    content = 'Sure, here you go:\n```json\n{"a": 1}\n```\n'
    assert _extract_json(content) == {"a": 1}


def test_extract_json_bare_code_fence_no_language_tag():
    content = '```\n{"a": 1}\n```'
    assert _extract_json(content) == {"a": 1}


def test_extract_json_think_block():
    content = "<think>reasoning about the answer...</think>\n" '{"a": 1}'
    assert _extract_json(content) == {"a": 1}


def test_extract_json_nested_braces_and_strings():
    content = '{"a": {"b": "a } string with a brace"}, "c": [1, 2]}'
    assert _extract_json(content) == {"a": {"b": "a } string with a brace"}, "c": [1, 2]}


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError):
        _extract_json("I cannot help with that request.")


def test_extract_json_unbalanced_raises():
    with pytest.raises(ValueError):
        _extract_json('{"a": 1')


class _FakeRateLimit(RateLimitError):
    """RateLimitError needs a response/body; this builds one cheaply."""

    def __init__(self):
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        response = httpx.Response(429, request=request)
        super().__init__("rate limited", response=response, body=None)


async def _no_sleep(seconds):
    """Patching asyncio.sleep with a lambda that calls asyncio.sleep recurses."""
    return None


@pytest.mark.asyncio
async def test_create_retries_a_rate_limit_and_returns_the_later_success(monkeypatch):
    """A single 429 used to destroy a whole authoring run: the exception
    escaped the drive loop, the session ended with no marks, and the run was
    reported as having never marked any data."""
    calls = []
    slept = []

    async def _fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _FakeRateLimit()
        return "ok"

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(client_module.client.chat.completions, "create", _fake_create)
    monkeypatch.setattr(client_module.asyncio, "sleep", _fake_sleep)

    assert await client_module._create(model="m") == "ok"
    assert len(calls) == 2
    assert slept == [client_module.RATE_LIMIT_SLEEP_S]


@pytest.mark.asyncio
async def test_create_gives_up_after_the_retry_budget(monkeypatch):
    calls = []

    async def _always_limited(**kwargs):
        calls.append(kwargs)
        raise _FakeRateLimit()

    async def _fake_sleep(seconds):
        pass

    monkeypatch.setattr(client_module.client.chat.completions, "create", _always_limited)
    monkeypatch.setattr(client_module.asyncio, "sleep", _fake_sleep)

    with pytest.raises(RateLimitError):
        await client_module._create(model="m")
    assert len(calls) == client_module.RATE_LIMIT_RETRIES


@pytest.mark.asyncio
async def test_create_does_not_retry_other_errors(monkeypatch):
    """Only rate limits are retried — paying three times for a real failure
    helps nobody."""
    calls = []

    async def _boom(**kwargs):
        calls.append(kwargs)
        raise ValueError("bad request")

    monkeypatch.setattr(client_module.client.chat.completions, "create", _boom)

    with pytest.raises(ValueError):
        await client_module._create(model="m")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_create_reports_a_rate_limit_wait_to_the_listener(monkeypatch):
    """The UI shows a quota wait in the status chip; without this signal a
    full minute of silence is indistinguishable from a hung run."""
    events = []
    calls = []

    async def _fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _FakeRateLimit()
        return "ok"

    async def _listener(waiting):
        events.append(waiting)

    monkeypatch.setattr(client_module.client.chat.completions, "create", _fake_create)
    monkeypatch.setattr(client_module.asyncio, "sleep", _no_sleep)
    token = client_module.rate_limit_listener.set(_listener)
    try:
        assert await client_module._create(model="m") == "ok"
    finally:
        client_module.rate_limit_listener.reset(token)

    assert events == [True, False], "the chip must be cleared once the call goes through"


@pytest.mark.asyncio
async def test_create_clears_the_rate_limit_flag_when_it_gives_up(monkeypatch):
    """Exhausting the budget still has to clear the chip, or the run ends with
    the UI stuck on 'Waiting for limit reset'."""
    events = []

    async def _always_limited(**kwargs):
        raise _FakeRateLimit()

    async def _listener(waiting):
        events.append(waiting)

    monkeypatch.setattr(client_module.client.chat.completions, "create", _always_limited)
    monkeypatch.setattr(client_module.asyncio, "sleep", _no_sleep)
    token = client_module.rate_limit_listener.set(_listener)
    try:
        with pytest.raises(RateLimitError):
            await client_module._create(model="m")
    finally:
        client_module.rate_limit_listener.reset(token)

    assert events == [True, False]


@pytest.mark.asyncio
async def test_create_is_fine_without_a_listener(monkeypatch):
    """Recorder and replay paths call the client outside an agent run."""
    calls = []

    async def _fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _FakeRateLimit()
        return "ok"

    monkeypatch.setattr(client_module.client.chat.completions, "create", _fake_create)
    monkeypatch.setattr(client_module.asyncio, "sleep", _no_sleep)
    assert await client_module._create(model="m") == "ok"
