import uuid

import pytest

from app.recorder.replay import _resolve_url, replay_workflow


def test_resolve_url_substitutes_named_param():
    assert _resolve_url("/search?q={query}", {"query": "television"}) == "/search?q=television"


def test_resolve_url_url_encodes_the_value():
    assert _resolve_url("/search?q={query}", {"query": "smart tv"}) == "/search?q=smart%20tv"


def test_resolve_url_leaves_unknown_placeholders_alone():
    assert _resolve_url("/search?q={missing}", {}) == "/search?q={missing}"


def test_resolve_url_without_placeholders_is_identity():
    assert _resolve_url("/search?q=fixed", {"query": "x"}) == "/search?q=fixed"


@pytest.mark.asyncio
async def test_replay_substitutes_param_into_goto_url(fixture_site_url):
    snapshot = {
        "steps": [
            {"type": "goto", "url": f"{fixture_site_url}/search.html?q=refrigerator",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "root": "li.product",
                "fields": [
                    {"name": "title", "selectors": [".title"], "take": "text"},
                    {"name": "price", "selectors": [".price"], "take": "text"},
                ],
            }
        },
    }
    result = await replay_workflow(
        snapshot, {"query": "television"}, None, uuid.uuid4(), headless=True
    )
    titles = [row["title"] for row in result["data"]]
    assert titles == ["Smart Television", "Basic Television"]
