"""Bot-wall detection.

The page-level tests run against tests/fixtures/site/cf-blocked.html, a trimmed
copy of the real Cloudflare block page, so the selectors are exercised against
the markup they actually meet rather than an invented approximation.
"""
import pytest

from app.recorder import blocked


class FakeResponse:
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers


def test_cloudflare_403_is_a_block():
    wall = blocked.from_response(FakeResponse(403, {
        "server": "cloudflare", "cf-ray": "a2b1111fa85b3a55-DAC",
    }))
    assert wall is not None
    assert wall.kind == "blocked"
    assert "403" in wall.detail
    assert "a2b1111fa85b3a55-DAC" in wall.detail


def test_cf_mitigated_header_is_a_challenge():
    wall = blocked.from_response(FakeResponse(200, {"cf-mitigated": "challenge"}))
    assert wall is not None
    assert wall.is_challenge


def test_the_sites_own_403_is_not_a_block():
    # A private page or an expired session — the caller must surface this as an
    # ordinary failure, not as "this site cannot be turned into an API".
    assert blocked.from_response(FakeResponse(403, {"server": "nginx"})) is None


def test_ordinary_errors_are_not_blocks():
    assert blocked.from_response(FakeResponse(404, {"server": "cloudflare"})) is None
    assert blocked.from_response(FakeResponse(500, {"server": "cloudflare"})) is None


def test_missing_response_is_not_a_block():
    # Playwright returns None for same-document navigations.
    assert blocked.from_response(None) is None


def test_block_and_challenge_messages_differ():
    block = blocked.Block("blocked", "HTTP 403 from Cloudflare")
    challenge = blocked.Block("challenge", "an interactive challenge is on screen")
    assert "cannot be turned into an API" in block.message()
    assert "clear the challenge" in challenge.message()


def test_the_alibaba_punish_url_is_a_challenge():
    # Daraz (and every other Alibaba property) redirects here instead of
    # answering with a block status, so nothing in the response says "wall".
    wall = blocked.from_url(
        "https://www.daraz.com.bd///_____tmd_____/punish?x5secdata=xfOPp6Eoq-_8URjUB20Ha"
    )
    assert wall is not None
    assert wall.is_challenge
    assert "x5sec" in wall.detail


def test_the_x5secdata_marker_alone_is_a_challenge():
    # Seen on other Alibaba properties without the /_____tmd_____/ path.
    assert blocked.from_url("https://example.com/verify?x5secdata=abc") is not None


def test_ordinary_daraz_urls_are_not_challenges():
    assert blocked.from_url("https://www.daraz.com.bd/") is None
    assert blocked.from_url("https://www.daraz.com.bd/catalog/?q=laptop") is None
    assert blocked.from_url(None) is None


@pytest.mark.asyncio
async def test_detects_the_rendered_punish_page(fixture_site_url, fixture_page):
    # Served from the fixture site, so the URL marker cannot fire — this
    # exercises the DOM signature on its own.
    await fixture_page.goto(f"{fixture_site_url}/x5sec-punish.html")
    wall = await blocked.from_page(fixture_page)
    assert wall is not None
    assert wall.is_challenge


@pytest.mark.asyncio
async def test_detects_the_real_block_page(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/cf-blocked.html")
    wall = await blocked.from_page(fixture_page)
    assert wall is not None
    assert wall.kind == "blocked"
    assert "Attention Required" in wall.detail


@pytest.mark.asyncio
async def test_an_ordinary_page_is_not_a_block(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    assert await blocked.from_page(fixture_page) is None


@pytest.mark.asyncio
async def test_detect_prefers_the_response(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    response = FakeResponse(403, {"server": "cloudflare"})
    wall = await blocked.detect(fixture_page, response)
    assert wall is not None and wall.kind == "blocked"
