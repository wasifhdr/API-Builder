import uuid

import pytest

from app.config import settings
from app.recorder.replay import ReplayError, replay_workflow


async def test_happy_path_goto_and_extract(fixture_site_url):
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "root": ".book-item",
                "fields": [
                    {"name": "title", "selector": ".book-title", "take": "text"},
                    {"name": "price", "selector": ".book-price", "take": "text", "transform": "number"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert len(result["data"]) == 3
    assert result["data"][0] == {"title": "Physics 101", "price": 350}


async def test_param_substitution_in_fill(fixture_site_url):
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "fill", "selectors": ["#search-input"], "value": {"param": "query"}},
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {"mode": "single", "fields": [{"name": "echoed", "selector": "#search-echo", "take": "text"}]}
        },
    }
    result = await replay_workflow(snapshot, {"query": "physics book"}, None, uuid.uuid4())
    assert result["data"] == {"echoed": "physics book"}


async def test_selector_fallback_uses_second_candidate(fixture_site_url):
    # First candidate doesn't exist on the page; replay must fall through to
    # the second one rather than failing outright.
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {
                "i": 1,
                "type": "fill",
                "selectors": ["#this-id-does-not-exist", "#search-input"],
                "value": {"literal": "fallback worked"},
            },
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {"mode": "single", "fields": [{"name": "echoed", "selector": "#search-echo", "take": "text"}]}
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"] == {"echoed": "fallback worked"}


async def test_all_selectors_missing_raises_replay_error_with_artifacts(fixture_site_url):
    execution_id = uuid.uuid4()
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "click", "selectors": ["#totally-bogus-selector"]},
        ],
        "extraction": {},
    }
    with pytest.raises(ReplayError) as exc_info:
        await replay_workflow(snapshot, {}, None, execution_id)

    artifact_dir = settings.failures_path / str(execution_id)
    assert artifact_dir.is_dir()
    assert (artifact_dir / "screenshot.png").exists()
    assert (artifact_dir / "page.html").exists()
    assert exc_info.value.artifact_path == str(artifact_dir)
