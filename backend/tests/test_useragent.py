"""The UA the recorder/replay browsers announce.

The token these tests strip is not cosmetic: waltonbd.com's Cloudflare edge
answers `HeadlessChrome/...` with a flat HTTP 403 (measured across six browser
configurations — it was the only variable that changed the outcome).
"""
import pytest

from app.recorder import useragent

HEADLESS_SHELL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) HeadlessChrome/149.0.7827.55 Safari/537.36"
)
# What HEADLESS_SHELL_UA must become: same major version, human token, reduced.
HUMANIZED_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
# A UA that is already in human form, for the idempotence check.
REAL_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def test_humanize_removes_the_headless_token():
    assert "Headless" not in useragent.humanize(HEADLESS_SHELL_UA)


def test_humanize_reduces_the_build_number():
    # Real Chrome sends <major>.0.0.0; only headless leaks a full build number,
    # so stripping the token alone would still leave an impossible UA.
    assert useragent.humanize(HEADLESS_SHELL_UA) == HUMANIZED_UA


def test_humanize_is_idempotent():
    assert useragent.humanize(REAL_CHROME_UA) == REAL_CHROME_UA


def test_humanize_leaves_the_platform_token_alone():
    mac = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) HeadlessChrome/149.0.7827.55 Safari/537.36"
    )
    assert "Macintosh; Intel Mac OS X 10_15_7" in useragent.humanize(mac)


@pytest.mark.asyncio
async def test_resolve_reads_the_real_browser_and_caches(monkeypatch):
    from playwright.async_api import async_playwright

    monkeypatch.setattr(useragent, "_cache", {})
    async with async_playwright() as pw:
        resolved = await useragent.resolve_user_agent(pw)
        assert "Headless" not in resolved
        assert resolved.startswith("Mozilla/5.0")

        # Second call must not launch anything — break the launcher to prove it.
        class Exploding:
            async def launch(self, **kwargs):
                raise AssertionError("resolve_user_agent relaunched a cached channel")

        class FakePW:
            chromium = Exploding()

        assert await useragent.resolve_user_agent(FakePW()) == resolved


@pytest.mark.asyncio
async def test_resolve_falls_back_when_the_probe_fails(monkeypatch):
    monkeypatch.setattr(useragent, "_cache", {})

    class Failing:
        async def launch(self, **kwargs):
            raise RuntimeError("no browser here")

    class FakePW:
        chromium = Failing()

    # A probe failure must not fail the recording — and must not leave us
    # announcing HeadlessChrome either.
    resolved = await useragent.resolve_user_agent(FakePW())
    assert resolved == useragent.FALLBACK_USER_AGENT
    assert "Headless" not in resolved
