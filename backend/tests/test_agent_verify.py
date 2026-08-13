import pytest

from app.agent.verify import verify_workflow

PLAN = {
    "parameters": [{
        "name": "query", "type": "string", "required": True,
        "drive_value": "refrigerator", "verify_value": "television",
        "description": None,
    }],
    "fields": [{"name": "title", "type": "string"}, {"name": "price", "type": "string"}],
}

EXTRACTION = {
    "main": {
        "mode": "list",
        "root": "li.product",
        "fields": [
            {"name": "title", "selectors": [".title"], "take": "text"},
            {"name": "price", "selectors": [".price"], "take": "text"},
        ],
    }
}


def _templated_snapshot(base: str) -> dict:
    return {
        "steps": [
            {"type": "goto",
             "url": f"{base}/search.html?q=refrigerator",
             "url_template": f"{base}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": EXTRACTION,
    }


def _hardcoded_snapshot(base: str) -> dict:
    return {
        "steps": [
            {"type": "goto", "url": f"{base}/search.html?q=refrigerator"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": EXTRACTION,
    }


DRIVE_DATA = [
    {"title": "Blue Refrigerator", "price": "45000"},
    {"title": "Silver Refrigerator", "price": "52000"},
]


@pytest.mark.asyncio
async def test_a_correct_workflow_passes_every_check(fixture_site_url):
    result = await verify_workflow(
        _templated_snapshot(fixture_site_url), PLAN, DRIVE_DATA
    )
    assert result.passed, [c.detail for c in result.checks if not c.passed]
    assert {row["title"] for row in result.data} == {"Smart Television", "Basic Television"}


@pytest.mark.asyncio
async def test_a_hardcoded_url_fails_the_differential_check(fixture_site_url):
    """The critical test: schema and row-count checks all pass, and the workflow
    is still broken because it ignores its parameter."""
    result = await verify_workflow(
        _hardcoded_snapshot(fixture_site_url), PLAN, DRIVE_DATA
    )
    assert not result.passed
    failed = [c.name for c in result.checks if not c.passed]
    assert failed == ["differs_from_drive"]


@pytest.mark.asyncio
async def test_missing_declared_field_fails(fixture_site_url):
    snapshot = _templated_snapshot(fixture_site_url)
    snapshot["extraction"]["main"]["fields"] = [
        {"name": "title", "selectors": [".title"], "take": "text"}
    ]
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)
    assert not result.passed
    assert "fields_present" in [c.name for c in result.checks if not c.passed]


@pytest.mark.asyncio
async def test_zero_rows_fails(fixture_site_url):
    snapshot = _templated_snapshot(fixture_site_url)
    snapshot["steps"][0]["url_template"] = f"{fixture_site_url}/search.html?q=nothingmatches"
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)
    assert not result.passed
    assert "has_rows" in [c.name for c in result.checks if not c.passed]


@pytest.mark.asyncio
async def test_replay_error_fails_the_first_check(fixture_site_url):
    snapshot = _templated_snapshot(fixture_site_url)
    snapshot["steps"].insert(1, {"type": "click", "selectors": ["#does-not-exist"]})
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)
    assert not result.passed
    assert result.checks[0].name == "replays"
    assert not result.checks[0].passed
