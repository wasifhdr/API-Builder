from app.recorder import selector_compiler as sc


class FakePage:
    """Stand-in Page: records evaluate() calls and returns queued results."""

    def __init__(self, eval_results, screenshot=b"png"):
        self._eval_results = list(eval_results)
        self._screenshot = screenshot
        self.evals = []

    async def evaluate(self, js, arg=None):
        self.evals.append((js, arg))
        return self._eval_results.pop(0)

    async def screenshot(self, clip=None):
        return self._screenshot


PICK = {
    "pick_id": "p1",
    "selectors": ["h3", "div > h3"],
    "preview": "Aparthotel Stare Miasto",
    "generalized": ".card",
    "outline": [{"tag": "h3", "id": "", "classes": ["title"], "data": {}, "role": "", "aria": "", "text": "Aparthotel"}],
    "rect": {"x": 0, "y": 0, "width": 100, "height": 20},
}
FIELD = {"name": "hotel_name", "description": "featured hotel name", "example": "Aparthotel", "take": "text"}


async def test_compile_from_pick_returns_validated_llm_selectors(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000, images=None):
        # The outline and example reached the prompt; a screenshot was attached.
        assert "featured hotel name" in user
        assert images and len(images) == 1
        return {"selectors": [".card .title", ".title"]}

    monkeypatch.setattr(sc, "complete_json", fake_complete_json)
    # Validation: both candidates resolve to the stamped element -> both True.
    page = FakePage(eval_results=[True, True])
    out = await sc.compile_from_pick(page, PICK, mode="single", root=None, field=FIELD)
    assert out[0] == ".card .title"
    assert ".title" in out


async def test_compile_from_pick_drops_invalid_candidates(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000, images=None):
        return {"selectors": [".wrong", ".card .title"]}

    monkeypatch.setattr(sc, "complete_json", fake_complete_json)
    # .wrong -> False (dropped), .card .title -> True (kept)
    page = FakePage(eval_results=[False, True])
    out = await sc.compile_from_pick(page, PICK, mode="single", root=None, field=FIELD)
    assert out == [".card .title"]


async def test_compile_from_pick_falls_back_to_heuristics_when_llm_down(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: False)
    # No LLM: validate the heuristic candidates ["h3", "div > h3"]; first valid.
    page = FakePage(eval_results=[True, True])
    out = await sc.compile_from_pick(page, PICK, mode="single", root=None, field=FIELD)
    assert out and out[0] == "h3"


async def test_reheal_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: False)
    page = FakePage(eval_results=[])
    out = await sc.reheal(page, mode="single", root=None, field=FIELD)
    assert out is None
