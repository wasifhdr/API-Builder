import pytest

from app.agent.observe import observe
from app.agent.tools import TOOL_SCHEMAS, dispatch, unmarked_fields

FIELDS = [{"name": "title"}, {"name": "price"}]


def test_unmarked_fields_lists_everything_when_nothing_is_marked():
    assert unmarked_fields([], FIELDS) == ["title", "price"]


def test_unmarked_fields_ignores_the_row_mark():
    assert unmarked_fields([{"ref": "ref_1"}], FIELDS) == ["title", "price"]


def test_unmarked_fields_maps_named_marks_by_name():
    marks = [{"ref": "ref_1"}, {"ref": "ref_3", "field": "price"}]
    assert unmarked_fields(marks, FIELDS) == ["title"]


def test_unmarked_fields_falls_back_to_positional_for_unnamed_marks():
    marks = [{"ref": "ref_1"}, {"ref": "ref_2"}]
    assert unmarked_fields(marks, FIELDS) == ["price"]


def test_unmarked_fields_is_empty_once_every_field_is_covered():
    marks = [{"ref": "ref_1"}, {"ref": "ref_2", "field": "title"},
             {"ref": "ref_3", "field": "price"}]
    assert unmarked_fields(marks, FIELDS) == []


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
    marks: list[dict] = []
    await dispatch(fixture_page, "mark_target", {"ref": "ref_0"}, marks)
    assert marks == [{"ref": "ref_0"}]


@pytest.mark.asyncio
async def test_mark_target_records_field_and_take(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html?q=fan"}, [])
    await observe(fixture_page, with_screenshot=False)
    # The row mark comes first: a field-named mark cannot be marks[0].
    marks: list[dict] = [{"ref": "ref_0"}]
    await dispatch(
        fixture_page, "mark_target",
        {"ref": "ref_0", "field": "url", "take": "attr:href"}, marks,
    )
    assert marks[1] == {"ref": "ref_0", "field": "url", "take": "attr:href"}


@pytest.mark.asyncio
async def test_first_mark_may_not_name_a_field(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html?q=fan"}, [])
    await observe(fixture_page, with_screenshot=False)
    marks: list[dict] = []
    outcome = await dispatch(
        fixture_page, "mark_target", {"ref": "ref_0", "field": "title"}, marks,
    )
    assert marks == []
    assert "repeating result row" in outcome.text


@pytest.mark.asyncio
async def test_done_is_refused_when_nothing_is_marked(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    outcome = await dispatch(fixture_page, "done", {}, [], fields=FIELDS)
    assert not outcome.finished
    assert "not marked any data" in outcome.text
    assert "title" in outcome.text and "price" in outcome.text


@pytest.mark.asyncio
async def test_done_is_refused_when_a_field_is_unmarked(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    marks = [{"ref": "ref_1"}, {"ref": "ref_2", "field": "title"}]
    outcome = await dispatch(fixture_page, "done", {}, marks, fields=FIELDS)
    assert not outcome.finished
    assert "price" in outcome.text


@pytest.mark.asyncio
async def test_done_succeeds_once_every_field_is_marked(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    marks = [{"ref": "ref_1"}, {"ref": "ref_2", "field": "title"},
             {"ref": "ref_3", "field": "price"}]
    assert (await dispatch(fixture_page, "done", {}, marks, fields=FIELDS)).finished


@pytest.mark.asyncio
async def test_done_is_honoured_when_enforcement_is_spent(fixture_site_url, fixture_page):
    """The driver stops enforcing after MAX_DONE_REFUSALS so a field that is
    genuinely absent cannot burn every remaining turn."""
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    marks = [{"ref": "ref_1"}, {"ref": "ref_2", "field": "title"}]
    outcome = await dispatch(
        fixture_page, "done", {}, marks, fields=FIELDS, enforce_marks=False,
    )
    assert outcome.finished


@pytest.mark.asyncio
async def test_a_spent_budget_still_refuses_done_with_nothing_marked(
    fixture_site_url, fixture_page,
):
    """The escape hatch is for a field that is absent, not for a drive that
    marked nothing — that cannot produce an API, so the remaining turns are
    always worth more than finishing empty. Answering `done` on repeat used to
    end the attempt with zero marks once the budget was spent."""
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    outcome = await dispatch(
        fixture_page, "done", {}, [], fields=FIELDS, enforce_marks=False,
    )
    assert not outcome.finished
    assert "not marked any data" in outcome.text


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
