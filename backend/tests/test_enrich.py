import pytest

from app.llm import enrich as enrich_module
from app.llm.spec_builder import build_skeleton


@pytest.fixture
def sample_spec():
    parameters = [{"name": "query", "type": "string", "required": True, "example": "physics"}]
    output_schema = {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}}}}
    return build_skeleton("Book Search", "book-search-ab12", None, parameters, output_schema), parameters


async def test_enrich_merges_successful_completion(monkeypatch, sample_spec):
    spec, parameters = sample_spec

    async def fake_complete_json(system, user, schema, max_tokens=1200):
        return {
            "api_description": "Searches for books by title.",
            "endpoint_summary": "Search books",
            "tags": ["books"],
            "param_query": "The search term to look up.",
        }

    monkeypatch.setattr(enrich_module, "complete_json", fake_complete_json)

    result = await enrich_module.enrich_spec(
        spec,
        "Book Search",
        "https://example.com",
        [{"type": "goto", "url": "https://example.com"}],
        parameters,
        [{"title": "a"}],
    )

    assert result["info"]["description"] == "Searches for books by title."
    op = result["paths"]["/v1/run/book-search-ab12"]["get"]
    assert op["summary"] == "Search books"
    assert op["tags"] == ["books"]
    assert op["parameters"][0]["description"] == "The search term to look up."


async def test_enrich_retries_once_then_raises(monkeypatch, sample_spec):
    spec, parameters = sample_spec
    call_count = 0

    async def failing_complete_json(system, user, schema, max_tokens=1200):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("LLM gateway unreachable")

    monkeypatch.setattr(enrich_module, "complete_json", failing_complete_json)

    with pytest.raises(RuntimeError):
        await enrich_module.enrich_spec(spec, "Book Search", "https://example.com", [], parameters, None)

    assert call_count == 2  # initial attempt + one retry
