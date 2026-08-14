"""Deriving a human-looking User-Agent from the browser we actually launch.

Chromium in headless mode announces `HeadlessChrome/<version>`, and that
substring alone is enough to be refused outright: waltonbd.com's Cloudflare
edge answers it with HTTP 403 and a "Sorry, you have been blocked" page before
any JS challenge runs, while the *same* headless binary with the token removed
gets a 200. Measured across six configurations — the token was the only
variable that mattered, and a warmed profile made no difference.

Headless also reports the full build number (`149.0.7827.55`) where real Chrome
reduces its UA to `<major>.0.0.0`, so dropping the token alone would still leave
a string no real Chrome ever sends. Both fixes live in `humanize`.

The UA is derived from the launched binary rather than pinned to a literal: a
hard-coded version drifts further from the truth on every Playwright update,
and eventually contradicts the `Sec-CH-UA` client hints the same browser sends
(which we cannot override). Deriving keeps the two in step for free.
"""
import logging
import re

log = logging.getLogger("recorder")

# Used only when the probe launch below fails — a plausible desktop Chrome, so
# a probe failure degrades to "slightly stale UA" rather than "announces
# HeadlessChrome and gets blocked".
FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# Chrome/149.0.7827.55 -> Chrome/149.0.0.0 (real Chrome's reduced UA form).
_FULL_VERSION_RE = re.compile(r"Chrome/(\d+)(?:\.\d+){3}")

# Probing costs a browser launch, so the answer is cached for the life of the
# process, keyed by channel (the bundled Chromium, `chromium`, and `chrome` are
# each a different build with a different version).
_cache: dict[str, str] = {}


def humanize(raw_user_agent: str) -> str:
    """Turns a launched browser's own UA into the one a human Chrome sends.

    Idempotent: a UA that is already in human form passes through unchanged.
    """
    ua = raw_user_agent.replace("HeadlessChrome/", "Chrome/")
    return _FULL_VERSION_RE.sub(lambda m: f"Chrome/{m.group(1)}.0.0.0", ua)


async def resolve_user_agent(pw, channel: str | None = None) -> str:
    """The UA to hand every context we open for `channel`.

    Launches a throwaway browser once per channel to read its real UA, then
    caches. Never raises — a failed probe falls back to FALLBACK_USER_AGENT,
    because being unable to read the UA is not a reason to fail a recording.
    """
    key = channel or "_bundled"
    cached = _cache.get(key)
    if cached is not None:
        return cached

    user_agent = FALLBACK_USER_AGENT
    try:
        launch_kwargs: dict = {"headless": True, "args": ["--disable-gpu"]}
        if channel:
            launch_kwargs["channel"] = channel
        browser = await pw.chromium.launch(**launch_kwargs)
        try:
            page = await browser.new_page()
            raw = await page.evaluate("() => navigator.userAgent")
            user_agent = humanize(raw)
        finally:
            await browser.close()
    except Exception as exc:  # noqa: BLE001 - any probe failure falls back
        log.warning("could not probe the browser user agent (%s); using fallback", exc)

    _cache[key] = user_agent
    return user_agent
