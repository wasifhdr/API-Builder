import uuid

from app.recorder import session as session_mod
from app.recorder.session import RecordingSession


def make_session():
    s = RecordingSession(str(uuid.uuid4()), str(uuid.uuid4()))
    s.published = []

    async def fake_publish(evt):
        s.published.append(evt)

    s._publish = fake_publish  # type: ignore[method-assign]
    s.page = object()  # only passed through to the (patched) compiler
    return s


async def test_compile_field_publishes_selectors(monkeypatch):
    s = make_session()
    s._last_pick = {
        "pick_id": "p1", "selectors": ["h3"], "preview": "Aparthotel",
        "generalized": ".card", "outline": [], "rect": None,
    }

    async def fake_compile(page, pick_ctx, *, mode, root, field):
        return [".card .title", ".title"]

    monkeypatch.setattr(session_mod, "compile_from_pick", fake_compile)
    await s._handle_command({
        "t": "compile_field", "mode": "single", "root": None,
        "name": "hotel_name", "description": "the hotel", "take": "text",
    })

    evt = s.published[-1]
    assert evt["t"] == "field_compiled"
    assert evt["field"]["name"] == "hotel_name"
    assert evt["field"]["selectors"] == [".card .title", ".title"]
    assert evt["field"]["example"] == "Aparthotel"


async def test_compile_root_publishes_roots(monkeypatch):
    s = make_session()
    s._last_pick = {"pick_id": "p1", "generalized": ".card", "outline": [], "rect": None, "selectors": []}

    async def fake_compile_root(page, pick_ctx):
        return [".card", ".list .card"]

    monkeypatch.setattr(session_mod, "compile_root_from_pick", fake_compile_root)
    await s._handle_command({"t": "compile_root"})

    evt = s.published[-1]
    assert evt["t"] == "root_compiled"
    assert evt["roots"] == [".card", ".list .card"]


async def test_compile_field_without_pick_is_noop(monkeypatch):
    s = make_session()
    s._last_pick = None
    await s._handle_command({"t": "compile_field", "mode": "single", "name": "x", "take": "text"})
    assert all(e["t"] != "field_compiled" for e in s.published)
