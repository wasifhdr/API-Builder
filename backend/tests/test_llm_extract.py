from app.recorder import llm_extract


class FakePage:
    """Minimal stand-in: llm_fill_missing only calls page.evaluate()."""

    def __init__(self, texts):
        self._texts = texts

    async def evaluate(self, js, arg):
        return self._texts


CONFIG = {
    "mode": "list",
    "root": ".row",
    "fields": [
        {"name": "field1", "selector": ".t", "take": "text", "transform": "none"},
        {"name": "field2", "selector": ".b", "take": "text", "transform": "none"},
    ],
}


async def test_fills_null_field_from_llm(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    captured = {}

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        captured["user"] = user
        return {"items": [{"index": 1, "field2": "recovered body"}]}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    data = [
        {"field1": "A", "field2": "body A"},
        {"field1": "B", "field2": None},
    ]
    page = FakePage(["A body A", "B the real body text"])
    out = await llm_extract.llm_fill_missing(page, CONFIG, data)

    assert out[1]["field2"] == "recovered body"  # gap filled
    assert out[0]["field2"] == "body A"  # working value untouched
    # Only the item with a gap is sent, and the working value seeds a few-shot example.
    assert "[index 1]" in captured["user"]
    assert "[index 0]" not in captured["user"]
    assert "body A" in captured["user"]


async def test_no_llm_call_when_nothing_missing(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    called = False

    async def fake_complete_json(*a, **k):
        nonlocal called
        called = True
        return {"items": []}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    data = [{"field1": "A", "field2": "body A"}]
    out = await llm_extract.llm_fill_missing(FakePage(["A body A"]), CONFIG, data)
    assert out == data
    assert called is False


async def test_noop_when_llm_disabled(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: False)
    data = [{"field1": "B", "field2": None}]
    out = await llm_extract.llm_fill_missing(FakePage(["B text"]), CONFIG, data)
    assert out == data


async def test_llm_failure_returns_data_unchanged(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(llm_extract, "complete_json", boom)

    data = [{"field1": "B", "field2": None}]
    out = await llm_extract.llm_fill_missing(FakePage(["B text"]), CONFIG, data)
    assert out == data  # graceful: replay still returns deterministic result
