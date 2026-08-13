import pytest
from playwright.async_api import async_playwright

from app.agent.tools import POST_ACTION_SETTLE_MS
from app.recorder.session import INJECTED_JS_PATH


def test_settle_exceeds_the_fill_debounce():
    """injected.js debounces `fill` by 400ms. Dropping below that reorders steps."""
    assert POST_ACTION_SETTLE_MS > 400


@pytest.fixture
def probe_url(fixture_site_url):
    return f"{fixture_site_url}/search.html"


async def _recording_page(pw, captured, url):
    browser = await pw.chromium.launch(headless=True, args=["--disable-gpu"])
    context = await browser.new_context()
    await context.expose_binding("__abEmit", lambda _s, event: captured.append(event))
    await context.add_init_script(INJECTED_JS_PATH.read_text(encoding="utf-8"))
    page = await context.new_page()
    # goto, NOT set_content: set_content does document.open()/write()/close(),
    # which wipes the document listeners the init script installed.
    await page.goto(url)
    return browser, page


@pytest.mark.asyncio
async def test_playwright_actions_are_captured_with_selectors(probe_url):
    captured: list[dict] = []
    async with async_playwright() as pw:
        browser, page = await _recording_page(pw, captured, probe_url)
        await page.fill("#q", "television")
        await page.wait_for_timeout(POST_ACTION_SETTLE_MS)
        await browser.close()

    assert len(captured) == 1
    assert captured[0]["type"] == "fill"
    assert captured[0]["value"] == "television"
    assert captured[0]["selectors"], "fill was captured without selector candidates"


@pytest.mark.asyncio
async def test_settling_between_actions_preserves_order(probe_url):
    captured: list[dict] = []
    async with async_playwright() as pw:
        browser, page = await _recording_page(pw, captured, probe_url)
        await page.fill("#q", "television")
        await page.wait_for_timeout(POST_ACTION_SETTLE_MS)  # what dispatch() does
        await page.click("button[type=submit]")
        await page.wait_for_timeout(POST_ACTION_SETTLE_MS)
        await browser.close()

    assert [e["type"] for e in captured] == ["fill", "click"]


@pytest.mark.asyncio
async def test_fill_emit_is_debounced_not_immediate(probe_url):
    """Isolates the actual mechanism POST_ACTION_SETTLE_MS protects against:
    injected.js does not emit `fill` synchronously, it delays ~400ms. This is
    checked directly rather than by racing fill against a second action's own
    CDP round trip — that race is inherently timing-dependent (both fill() and
    click() involve their own variable-latency browser round trips) and gave
    inconsistent orderings across fixture pages during manual verification, so
    asserting a specific race outcome would be a flaky, environment-dependent
    test. The debounce itself is what's real and worth pinning."""
    captured: list[dict] = []
    async with async_playwright() as pw:
        browser, page = await _recording_page(pw, captured, probe_url)
        await page.fill("#q", "television")
        assert captured == [], "fill must not emit synchronously — it is debounced"
        await page.wait_for_timeout(700)
        await browser.close()

    assert [e["type"] for e in captured] == ["fill"]
