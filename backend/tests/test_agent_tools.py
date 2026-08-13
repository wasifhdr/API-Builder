import pytest

from app.agent.observe import observe
from app.agent.tools import TOOL_SCHEMAS, dispatch


def test_tool_schemas_cover_the_documented_surface():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {
        "navigate", "click", "fill", "press", "scroll",
        "mark_target", "done", "give_up",
    }


def test_every_tool_schema_is_well_formed():
    for tool in TOOL_SCHEMAS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_navigate_loads_the_page(fixture_site_url, fixture_page):
    outcome = await dispatch(
        fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, []
    )
    assert not outcome.finished
    assert outcome.observation is not None
    assert outcome.observation.url.endswith("/search.html")


@pytest.mark.asyncio
async def test_fill_then_click_runs_the_search(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    obs = await observe(fixture_page, with_screenshot=False)

    input_ref = next(
        line.split("]")[0].strip("[") for line in obs.tree.splitlines()
        if "<input" in line
    )
    await dispatch(fixture_page, "fill", {"ref": input_ref, "value": "television"}, [])
    assert await fixture_page.input_value("#q") == "television"


@pytest.mark.asyncio
async def test_unknown_ref_is_reported_not_raised(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    outcome = await dispatch(fixture_page, "click", {"ref": "ref_9999"}, [])
    assert "re-observe" in outcome.text
    assert not outcome.finished


@pytest.mark.asyncio
async def test_mark_target_records_the_ref(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html?q=fan"}, [])
    await observe(fixture_page, with_screenshot=False)
    marks: list[str] = []
    await dispatch(fixture_page, "mark_target", {"ref": "ref_0"}, marks)
    assert marks == ["ref_0"]


@pytest.mark.asyncio
async def test_done_and_give_up_terminate(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    assert (await dispatch(fixture_page, "done", {}, [])).finished
    gave = await dispatch(fixture_page, "give_up", {"reason": "login wall"}, [])
    assert gave.finished and gave.gave_up and "login wall" in gave.text


@pytest.mark.asyncio
async def test_unknown_tool_is_reported(fixture_site_url, fixture_page):
    outcome = await dispatch(fixture_page, "teleport", {}, [])
    assert "unknown tool" in outcome.text.lower()
    assert not outcome.finished
