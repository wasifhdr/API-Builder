import pytest

from app.llm import authoring


def _steps():
    return [
        {"i": 0, "type": "goto", "url": "https://example.com"},
        {"i": 1, "type": "fill", "selectors": ["#q"], "value": {"literal": "python"}},
        {"i": 2, "type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}},
        {"i": 3, "type": "click", "selectors": [".submit"]},
        {"i": 4, "type": "fill", "selectors": ["#already-marked"], "value": {"param": "existing"}},
    ]


# ---------------------------------------------------------------------------
# suggest_parameters
# ---------------------------------------------------------------------------


async def test_suggest_parameters_happy_path(monkeypatch):
    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {
            "parameters": [
                {
                    "step_i": 1,
                    "name": "query",
                    "type": "string",
                    "example": "python",
                    "description": "Search term",
                    "confidence": 0.9,
                },
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_parameters(_steps())

    assert result == [
        {
            "step_i": 1,
            "name": "query",
            "type": "string",
            "example": "python",
            "description": "Search term",
            "confidence": 0.9,
        }
    ]


async def test_suggest_parameters_drops_hallucinated_step_i(monkeypatch):
    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {
            "parameters": [
                {"step_i": 999, "name": "bogus", "type": "string", "example": "x",
                 "description": None, "confidence": 0.5},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_parameters(_steps())

    assert result == []


async def test_suggest_parameters_skips_already_marked_steps(monkeypatch):
    seen_prompts = []

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        seen_prompts.append(user)
        # Even if the model hallucinates a suggestion for the already-marked
        # step's index, it must not survive validation (not a candidate).
        return {
            "parameters": [
                {"step_i": 4, "name": "existing2", "type": "string", "example": "x",
                 "description": None, "confidence": 0.5},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_parameters(_steps())

    assert result == []
    assert "step_i=4" not in seen_prompts[0]


async def test_suggest_parameters_redacts_password_like_fields(monkeypatch):
    seen_prompts = []

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        seen_prompts.append(user)
        return {
            "parameters": [
                {"step_i": 2, "name": "password", "type": "string", "example": "hunter2",
                 "description": None, "confidence": 0.9},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_parameters(_steps())

    # The password step's literal must never reach the prompt...
    assert "hunter2" not in seen_prompts[0]
    assert "step_i=2" not in seen_prompts[0]
    # ...and even a hallucinated suggestion for it is dropped (not a candidate).
    assert result == []


async def test_suggest_parameters_no_candidates_skips_llm_call(monkeypatch):
    called = False

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        nonlocal called
        called = True
        return {"parameters": []}

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    steps = [{"i": 0, "type": "click", "selectors": [".btn"]}]
    result = await authoring.suggest_parameters(steps)

    assert result == []
    assert called is False


async def test_suggest_parameters_retries_once_then_raises(monkeypatch):
    call_count = 0

    async def failing_complete_json(system, user, schema, max_tokens=2000):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("gateway unreachable")

    monkeypatch.setattr(authoring, "complete_json", failing_complete_json)

    with pytest.raises(RuntimeError):
        await authoring.suggest_parameters(_steps())

    assert call_count == 2


async def test_suggest_parameters_invalid_type_falls_back_to_string(monkeypatch):
    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {
            "parameters": [
                {"step_i": 1, "name": "query", "type": "not-a-real-type", "example": "python",
                 "description": None, "confidence": 0.9},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_parameters(_steps())

    assert result[0]["type"] == "string"


# ---------------------------------------------------------------------------
# suggest_extraction_fields
# ---------------------------------------------------------------------------


def _book_list_config():
    return {
        "mode": "list",
        "root": ".book-item",
        "fields": [
            {"name": "field1", "selector": ".book-title", "take": "text", "transform": "none"},
            {"name": "field2", "selector": ".book-price", "take": "text", "transform": "none"},
            {"name": "field3", "selector": "a", "take": "text", "transform": "none"},
        ],
    }


def _book_list_sample():
    return [
        {"field1": "The Great Gatsby", "field2": "$12.99", "field3": "/books/gatsby"},
        {"field1": "Moby Dick", "field2": "$9.99", "field3": "/books/moby-dick"},
    ]


async def test_suggest_extraction_fields_happy_path(monkeypatch):
    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {
            "fields": [
                {"selector": ".book-title", "name": "title", "take": "text", "transform": "none"},
                {"selector": ".book-price", "name": "price", "take": "text", "transform": "number"},
                {"selector": "a", "name": "url", "take": "attr:href", "transform": "abs_url"},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_extraction_fields(_book_list_config(), _book_list_sample())

    assert result == [
        {"selector": ".book-title", "name": "title", "take": "text", "transform": "none"},
        {"selector": ".book-price", "name": "price", "take": "text", "transform": "number"},
        {"selector": "a", "name": "url", "take": "attr:href", "transform": "abs_url"},
    ]


async def test_suggest_extraction_fields_drops_hallucinated_selector(monkeypatch):
    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {
            "fields": [
                {"selector": ".not-a-real-field", "name": "bogus", "take": "text", "transform": "none"},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_extraction_fields(_book_list_config(), _book_list_sample())

    assert result == []


async def test_suggest_extraction_fields_invalid_take_dropped_invalid_transform_defaults(monkeypatch):
    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {
            "fields": [
                {"selector": ".book-title", "name": "title", "take": "not-a-take", "transform": "none"},
                {"selector": ".book-price", "name": "price", "take": "text", "transform": "not-a-transform"},
            ],
        }

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_extraction_fields(_book_list_config(), _book_list_sample())

    assert result == [{"selector": ".book-price", "name": "price", "take": "text", "transform": "none"}]


async def test_suggest_extraction_fields_no_fields_skips_llm_call(monkeypatch):
    called = False

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        nonlocal called
        called = True
        return {"fields": []}

    monkeypatch.setattr(authoring, "complete_json", fake_complete_json)

    result = await authoring.suggest_extraction_fields({"mode": "single", "fields": []}, {})

    assert result == []
    assert called is False
