import re

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from app.agent.extract import ExtractionError, build_extraction, pick_context_for_ref
from app.agent.observe import observe
from app.recorder.session import INJECTED_JS_PATH

PLAN = {
    "parameters": [],
    "fields": [{"name": "title", "type": "string"}, {"name": "price", "type": "string"}],
}


@pytest_asyncio.fixture
async def recorder_page(fixture_site_url):
    # extract.py calls window.__abBuildPickResult, which only exists once
    # injected.js has run — conftest's plain fixture_page has no recorder
    # script, so this fixture mirrors test_agent_recorder_capture.py's local
    # setup instead of the shared one.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--disable-gpu"])
        context = await browser.new_context()
        await context.add_init_script(INJECTED_JS_PATH.read_text(encoding="utf-8"))
        page = await context.new_page()
        await page.goto(f"{fixture_site_url}/index.html")
        yield page
        await browser.close()


_REF_LABEL_RE = re.compile(r"\[(ref_\d+)\]")


def _refs_by_label(tree: str, label: str) -> list[str]:
    refs = []
    for line in tree.splitlines():
        if label in line:
            match = _REF_LABEL_RE.search(line)
            if match:
                refs.append(match.group(1))
    return refs


async def _mark_row_and_fields(page, fixture_site_url) -> list[str]:
    """Marks the container row first, then the title and price leaves under
    it, matching the two-step sequence the drive prompt requires."""
    await page.goto(f"{fixture_site_url}/search.html?q=television")
    obs = await observe(page, with_screenshot=False)

    row_ref = _refs_by_label(obs.tree, "li.product")[0]
    field_lines = [
        line for line in obs.tree.splitlines() if f"field inside {row_ref}" in line
    ]
    title_ref = next(
        _REF_LABEL_RE.search(line).group(1) for line in field_lines if "Smart Television" in line
    )
    price_ref = next(
        _REF_LABEL_RE.search(line).group(1) for line in field_lines if "38000" in line
    )
    return [row_ref, title_ref, price_ref]


@pytest.mark.asyncio
async def test_pick_context_has_the_compiler_required_keys(fixture_site_url, recorder_page):
    marks = await _mark_row_and_fields(recorder_page, fixture_site_url)
    ctx = await pick_context_for_ref(recorder_page, marks[1])
    assert ctx["pick_id"]
    assert ctx["selectors"]


@pytest.mark.asyncio
async def test_pick_context_stamps_the_element(fixture_site_url, recorder_page):
    marks = await _mark_row_and_fields(recorder_page, fixture_site_url)
    ctx = await pick_context_for_ref(recorder_page, marks[1])
    stamped = await recorder_page.query_selector(f'[data-ab-pick="{ctx["pick_id"]}"]')
    assert stamped is not None


@pytest.mark.asyncio
async def test_build_extraction_produces_a_list_config(fixture_site_url, recorder_page):
    marks = await _mark_row_and_fields(recorder_page, fixture_site_url)
    config = await build_extraction(recorder_page, marks, PLAN)

    main = config["main"]
    assert main["mode"] == "list"
    assert main["root"]
    assert {f["name"] for f in main["fields"]} == {"title", "price"}
    for field in main["fields"]:
        assert field["selectors"], f"{field['name']} compiled to no selectors"


@pytest.mark.asyncio
async def test_built_config_actually_extracts(fixture_site_url, recorder_page):
    from app.recorder.extraction import run_extraction

    marks = await _mark_row_and_fields(recorder_page, fixture_site_url)
    config = await build_extraction(recorder_page, marks, PLAN)
    rows = await run_extraction(recorder_page, config["main"])

    assert len(rows) >= 2
    assert {r["title"] for r in rows} == {"Smart Television", "Basic Television"}


@pytest.mark.asyncio
async def test_missing_field_marks_degrade_gracefully(fixture_site_url, recorder_page):
    """Only the row is marked, no individual fields — the compiler can't
    validate a field selector against the row's own pick_id (it would have to
    resolve to the row itself), so those fields come back with no selectors
    rather than a nonsense one. build_extraction still succeeds as long as at
    least one field has a usable selector... here none do, so it raises,
    which is the correct signal for verify to catch upstream."""
    await recorder_page.goto(f"{fixture_site_url}/search.html?q=fan")
    obs = await observe(recorder_page, with_screenshot=False)
    row_ref = _refs_by_label(obs.tree, "li.product")[0]

    with pytest.raises(ExtractionError, match="no declared field"):
        await build_extraction(recorder_page, [row_ref], PLAN)


@pytest.mark.asyncio
async def test_no_marks_raises(fixture_site_url, recorder_page):
    await recorder_page.goto(f"{fixture_site_url}/search.html?q=fan")
    with pytest.raises(ExtractionError, match="no extraction target"):
        await build_extraction(recorder_page, [], PLAN)


@pytest.mark.asyncio
async def test_stale_ref_raises(fixture_site_url, recorder_page):
    await recorder_page.goto(f"{fixture_site_url}/search.html?q=fan")
    with pytest.raises(ExtractionError):
        await build_extraction(recorder_page, ["ref_9999"], PLAN)
