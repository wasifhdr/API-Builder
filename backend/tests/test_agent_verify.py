from unittest.mock import AsyncMock, patch

import pytest

from app.agent.verify import (
    content_anchored_fallbacks,
    missing_field_names,
    verify_workflow,
)

FIELDS = [{"name": "title"}, {"name": "price"}]

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


def test_all_null_rows_count_as_missing_every_field():
    """The Walton defect: every row dict carries every declared key, so key
    presence is not evidence that anything was extracted."""
    rows = [{"title": None, "price": None}, {"title": None, "price": None}]
    assert missing_field_names(rows, FIELDS) == ["title", "price"]


def test_a_key_present_but_null_is_not_a_present_field():
    rows = [{"title": "Smart Television", "price": None}]
    assert missing_field_names(rows, FIELDS) == ["price"]


def test_a_field_populated_in_only_one_row_is_present():
    """Sparse data is normal — a discount price on 1 of 3 rows is not a defect,
    so no fill-rate threshold is applied."""
    rows = [
        {"title": "a", "price": None},
        {"title": "b", "price": "99"},
        {"title": "c", "price": None},
    ]
    assert missing_field_names(rows, FIELDS) == []


def test_a_blank_string_is_missing():
    assert missing_field_names([{"title": "   ", "price": "1"}], FIELDS) == ["title"]


def test_a_single_dict_is_treated_as_one_row():
    assert missing_field_names({"title": "x", "price": None}, FIELDS) == ["price"]


def test_no_rows_means_every_field_missing():
    assert missing_field_names([], FIELDS) == ["title", "price"]
    assert missing_field_names(None, FIELDS) == ["title", "price"]


@pytest.mark.asyncio
async def test_all_null_rows_fail_verification(fixture_site_url):
    """End to end: rows exist, keys exist, every value is null. Before this
    change the run passed every check and published."""
    snapshot = {
        "steps": [
            {"type": "goto",
             "url": f"{fixture_site_url}/search.html?q=refrigerator",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": {"main": {
            "mode": "list",
            "root": "li.product",
            "fields": [
                {"name": "title", "selectors": [".no-such-title"], "take": "text"},
                {"name": "price", "selectors": [".no-such-price"], "take": "text"},
            ],
        }},
    }
    # The extraction path calls the LLM to fill nulls; pin it to a no-op so the
    # test asserts the CHECK, not the model's behaviour.
    async def _no_fill(page, config, data):
        return data

    with patch("app.recorder.replay.llm_fill_missing", AsyncMock(side_effect=_no_fill)):
        result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)

    assert not result.passed
    check = next(c for c in result.checks if c.name == "fields_present")
    assert not check.passed
    assert "title" in check.detail and "price" in check.detail


def test_a_skipped_text_selector_is_content_anchored():
    fallbacks = [{"step_index": 3,
                  "skipped": ['a:has-text("WNR-6D6-GDFS-DI")'],
                  "used": "#products > div:nth-of-type(1) > a"}]
    assert content_anchored_fallbacks(fallbacks) == fallbacks


def test_a_skipped_href_selector_is_content_anchored():
    fallbacks = [{"step_index": 3,
                  "skipped": ['a[href="/product/4521"]'],
                  "used": "#products > div:nth-of-type(1) > a"}]
    assert content_anchored_fallbacks(fallbacks) == fallbacks


def test_a_skipped_class_selector_is_a_flake_not_a_defect():
    """Narrowing to content-anchored candidates is a false-positive filter: an
    id/class candidate missing for timing reasons must not cost an attempt."""
    fallbacks = [{"step_index": 2,
                  "skipped": ["button.search-btn"],
                  "used": "#search-form > button"}]
    assert content_anchored_fallbacks(fallbacks) == []


def test_no_fallbacks_is_no_finding():
    assert content_anchored_fallbacks([]) == []


def test_a_fallback_with_no_skipped_list_is_ignored():
    assert content_anchored_fallbacks([{"step_index": 1, "used": "#x"}]) == []


@pytest.mark.asyncio
async def test_a_step_anchored_to_drive_content_fails_verification(fixture_site_url):
    """Replays with `television` a workflow recorded against `refrigerator`.
    The text-anchored click cannot match, a positional candidate does, and the
    run must be rejected rather than quietly proceeding on the wrong element."""
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto",
             "url": f"{fixture_site_url}/search.html?q=refrigerator",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"i": 1, "type": "click", "selectors": [
                'a:has-text("Blue Refrigerator")',
                "#results > li:nth-of-type(1)",
            ]},
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": EXTRACTION,
    }
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)

    assert not result.passed
    check = next(c for c in result.checks if c.name == "stable_selectors")
    assert not check.passed
    assert "step 1" in check.detail
