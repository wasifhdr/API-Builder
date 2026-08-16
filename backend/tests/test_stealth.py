"""The client hints the browser announces alongside the spoofed User-Agent.

Overriding `user_agent` rewrites the UA header and `navigator.userAgent` — it
does NOT touch `Sec-CH-UA` or `navigator.userAgentData`, which keep announcing
`HeadlessChrome`. Measured against daraz.com.bd: a headless persistent context
carrying that contradiction is redirected to Alibaba's x5sec punish page on the
first navigation (blocked 6/6 across four launch configurations), while the same
context with the hints corrected loads the site and completes a search (2/2).
"""
import pytest

from app.recorder import stealth

HEADLESS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


def test_brands_never_mention_headless():
    brands = stealth.client_hint_metadata(HEADLESS_UA)["brands"]
    assert not any("headless" in b["brand"].lower() for b in brands)


def test_brands_carry_the_uas_major_version():
    meta = stealth.client_hint_metadata(HEADLESS_UA)
    assert {b["version"] for b in meta["brands"] if "Brand" not in b["brand"]} == {"149"}
    # A full version that disagrees with the UA string is its own tell.
    assert meta["fullVersion"] == "149.0.0.0"


def test_platform_follows_the_user_agent():
    # Announcing Windows hints under a macOS UA is more detectable than the
    # headless token it replaces.
    assert stealth.client_hint_metadata(HEADLESS_UA)["platform"] == "Windows"
    assert stealth.client_hint_metadata(MAC_UA)["platform"] == "macOS"


def test_an_unparseable_ua_still_yields_usable_metadata():
    meta = stealth.client_hint_metadata("something that is not a chrome ua")
    assert meta["brands"] and not any("headless" in b["brand"].lower() for b in meta["brands"])


@pytest.mark.asyncio
async def test_the_override_removes_headless_from_the_wire(fixture_site_url):
    """The end the site actually sees: request headers and JS both."""
    from playwright.async_api import async_playwright

    from app.recorder import useragent

    async with async_playwright() as pw:
        user_agent = await useragent.resolve_user_agent(pw)
        browser = await pw.chromium.launch(headless=True, args=stealth.launch_args(True))
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        headers: dict = {}

        def _capture(request):
            if request.is_navigation_request() and not headers:
                headers.update({k.lower(): v for k, v in request.headers.items()})

        await stealth.apply_client_hints(context, page, user_agent)
        page.on("request", _capture)
        await page.goto(f"{fixture_site_url}/index.html")

        assert "headless" not in headers.get("sec-ch-ua", "").lower()
        brands = await page.evaluate(
            "() => (navigator.userAgentData ? navigator.userAgentData.brands : [])"
            ".map(b => b.brand).join(',')"
        )
        assert "headless" not in brands.lower()
        await context.close()
        await browser.close()


@pytest.mark.asyncio
async def test_a_failed_override_never_breaks_the_session(fixture_site_url):
    # Being unable to set the hints is not a reason to fail a recording, for
    # the same reason a failed UA probe isn't (see app.recorder.useragent).
    class Exploding:
        async def new_cdp_session(self, page):
            raise RuntimeError("no CDP here")

    await stealth.apply_client_hints(Exploding(), object(), HEADLESS_UA)
