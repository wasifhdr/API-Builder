import uuid

from app.recorder.session import RecordingSession


def _make_session():
    """A bare RecordingSession with _publish stubbed out — _handle_mark_param
    only mutates self.steps/self.parameters and publishes an event, so this
    avoids needing a real Redis connection, DB, or Playwright browser."""
    events: list[dict] = []
    session = RecordingSession(str(uuid.uuid4()), str(uuid.uuid4()))

    async def fake_publish(event: dict) -> None:
        events.append(event)

    session._publish = fake_publish
    return session, events


async def test_mark_param_bare_command_defaults_to_string_and_none_description():
    session, events = _make_session()
    session.steps = [{"i": 0, "type": "fill", "selectors": ["#q"], "value": {"literal": "python"}}]

    await session._handle_mark_param({"t": "mark_param", "step_i": 0, "name": "query"})

    assert session.parameters == [{
        "name": "query",
        "type": "string",
        "required": True,
        "example": "python",
        "description": None,
        "source_step": 0,
    }]
    assert session.steps[0]["value"] == {"param": "query"}
    assert events[-1]["t"] == "param_marked"


async def test_mark_param_honors_type_and_description():
    session, _ = _make_session()
    session.steps = [{"i": 0, "type": "fill", "selectors": ["#page"], "value": {"literal": "2"}}]

    await session._handle_mark_param({
        "t": "mark_param",
        "step_i": 0,
        "name": "page",
        "type": "integer",
        "description": "Page number",
    })

    assert session.parameters[0]["type"] == "integer"
    assert session.parameters[0]["description"] == "Page number"


async def test_mark_param_invalid_type_falls_back_to_string():
    session, _ = _make_session()
    session.steps = [{"i": 0, "type": "fill", "selectors": ["#q"], "value": {"literal": "x"}}]

    await session._handle_mark_param({"t": "mark_param", "step_i": 0, "name": "q", "type": "not-a-real-type"})

    assert session.parameters[0]["type"] == "string"


async def test_mark_param_non_string_description_becomes_none():
    session, _ = _make_session()
    session.steps = [{"i": 0, "type": "fill", "selectors": ["#q"], "value": {"literal": "x"}}]

    await session._handle_mark_param({"t": "mark_param", "step_i": 0, "name": "q", "description": 123})

    assert session.parameters[0]["description"] is None


async def test_mark_param_select_option_step_supported():
    session, _ = _make_session()
    session.steps = [
        {"i": 0, "type": "select_option", "selectors": ["#size"], "value": {"literal": "M"}},
    ]

    await session._handle_mark_param({"t": "mark_param", "step_i": 0, "name": "size", "type": "string"})

    assert session.parameters[0] == {
        "name": "size",
        "type": "string",
        "required": True,
        "example": "M",
        "description": None,
        "source_step": 0,
    }
