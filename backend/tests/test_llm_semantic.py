from app.recorder import llm_extract


class FakePage:
    """Minimal stand-in: semantic_extract only calls page.evaluate()."""

    def __init__(self, payload):
        self._payload = payload

    async def evaluate(self, js, arg):
        return self._payload


SINGLE_CONFIG = {
    "mode": "single",
    "engine": "llm",
    "fields": [
        {"name": "hotel_name", "description": "name of the first featured hotel"},
        {"name": "location", "description": "city and country"},
        {"name": "price", "description": "starting price in BDT", "transform": "number"},
    ],
}


async def test_single_mode_maps_named_fields_and_transforms(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)
    captured = {}

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        captured["user"] = user
        return {
            "hotel_name": "Aparthotel Stare Miasto",
            "location": "Old Town, Poland, Krakow",
            "price": "BDT 14,049",
        }

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    page = FakePage({
        "title": "Booking.com",
        "url": "https://www.booking.com/",
        "text": "Homes guests love\nAparthotel Stare Miasto\nOld Town, Poland, Krakow\nStarting from BDT 14,049",
    })
    out = await llm_extract.semantic_extract(page, SINGLE_CONFIG)

    assert out["hotel_name"] == "Aparthotel Stare Miasto"
    assert out["location"] == "Old Town, Poland, Krakow"
    assert out["price"] == 14049  # number transform applied after the LLM
    # The field description reaches the prompt so the model knows what "price" means.
    assert "starting price in BDT" in captured["user"]


async def test_returns_none_when_llm_not_configured(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: False)
    out = await llm_extract.semantic_extract(FakePage({"title": "", "url": "", "text": "x"}), SINGLE_CONFIG)
    assert out is None


async def test_returns_none_on_llm_error(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(llm_extract, "complete_json", boom)
    out = await llm_extract.semantic_extract(FakePage({"title": "", "url": "", "text": "x"}), SINGLE_CONFIG)
    assert out is None  # caller will fall back to the selector path


async def test_list_mode_fills_every_item_by_index(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {"items": [{"index": 0, "name": "Alice"}, {"index": 1, "name": "Bob"}]}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    config = {
        "mode": "list",
        "engine": "llm",
        "root": ".card",
        "fields": [{"name": "name", "description": "person name"}],
    }
    # In list mode the only page.evaluate call is _item_texts → returns the row texts.
    page = FakePage(["Alice — engineer", "Bob — designer"])
    out = await llm_extract.semantic_extract(page, config)
    assert out == [{"name": "Alice"}, {"name": "Bob"}]
