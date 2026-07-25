import uuid

from app.recorder.session import RecordingSession


def _make_session(n_steps: int = 6):
    """A bare RecordingSession with _publish stubbed out — undo_step only
    mutates self.steps/self.parameters and publishes an event, so this needs
    no Redis, DB, or browser."""
    events: list[dict] = []
    session = RecordingSession(str(uuid.uuid4()), str(uuid.uuid4()))

    async def fake_publish(event: dict) -> None:
        events.append(event)

    session._publish = fake_publish
    session.steps = [
        {"i": i, "type": "click", "selectors": [f"#b{i}"]} for i in range(n_steps)
    ]
    return session, events


async def test_undo_step_publishes_renumbered_steps():
    session, events = _make_session(3)

    await session._handle_command({"t": "undo_step", "i": 0})

    evt = events[-1]
    assert evt["t"] == "step_removed"
    assert evt["i"] == 0
    # The client cannot renumber correctly on its own — the event must carry the
    # authoritative post-removal list, or its indices drift out of sync.
    assert [s["i"] for s in evt["steps"]] == [0, 1]
    assert [s["selectors"] for s in evt["steps"]] == [["#b1"], ["#b2"]]


async def test_repeated_undo_from_published_state_removes_every_step():
    """The reported bug: a client that mirrors the published events must be able
    to undo every step. With stale indices the backend's bounds check silently
    drops the command after a few undos."""
    session, events = _make_session(6)
    client_steps = [dict(s) for s in session.steps]

    for _ in range(6):
        # Always undo the last step the client can see.
        target = client_steps[-1]["i"]
        before = len(session.steps)
        await session._handle_command({"t": "undo_step", "i": target})
        assert len(session.steps) == before - 1, f"undo of step {target} was dropped"
        client_steps = [dict(s) for s in events[-1]["steps"]]

    assert session.steps == []


async def test_repeated_undo_of_first_step_removes_every_step():
    session, events = _make_session(6)
    client_steps = [dict(s) for s in session.steps]

    for _ in range(6):
        target = client_steps[0]["i"]
        before = len(session.steps)
        await session._handle_command({"t": "undo_step", "i": target})
        assert len(session.steps) == before - 1, f"undo of step {target} was dropped"
        client_steps = [dict(s) for s in events[-1]["steps"]]

    assert session.steps == []


async def test_undo_drops_parameter_bound_to_removed_step_and_repoints_later_ones():
    session, events = _make_session(0)
    session.steps = [
        {"i": 0, "type": "fill", "selectors": ["#q"], "value": {"param": "query"}},
        {"i": 1, "type": "click", "selectors": ["#go"]},
        {"i": 2, "type": "fill", "selectors": ["#p"], "value": {"param": "page"}},
    ]
    session.parameters = [
        {"name": "query", "type": "string", "required": True, "example": "x",
         "description": None, "source_step": 0},
        {"name": "page", "type": "integer", "required": True, "example": "2",
         "description": None, "source_step": 2},
    ]

    await session._handle_command({"t": "undo_step", "i": 0})

    # 'query' has no step left to fill it; 'page' moved from index 2 to 1.
    assert [p["name"] for p in session.parameters] == ["page"]
    assert session.parameters[0]["source_step"] == 1
    assert events[-1]["parameters"] == session.parameters


async def test_undo_out_of_range_index_is_ignored():
    session, events = _make_session(2)

    await session._handle_command({"t": "undo_step", "i": 5})

    assert len(session.steps) == 2
    assert events == []
