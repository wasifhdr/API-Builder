"""Recognising a bot-protection wall instead of driving into it blindly.

Without this, a blocked page is just a page: the agent observes a 689-character
document with one link, spends LLM turns trying to find a search box on it,
gives up with an unrelated reason, and the runner pays for two more attempts
that fail identically. Replay has the same problem more quietly — the block
surfaces as "none of the candidate selectors matched".

Two outcomes are distinguished because they mean different things for the
product:

- `blocked`  — the edge refused the request outright (HTTP 403 + a Cloudflare
  error page). Nothing on our side can proceed, and retrying is pointless.
- `challenge` — an interactive challenge is on screen. Also unattended-fatal,
  but a human driving the headful recorder can clear it themselves, so the
  message says so.

Selectors and titles below were taken from a live block page, not from memory.
"""
from dataclasses import dataclass

# Cloudflare's block/error page (`#cf-wrapper` wraps it, `#cf-error-details`
# holds the "Sorry, you have been blocked" headline).
_BLOCK_SELECTORS = ("#cf-error-details", "#cf-wrapper")

# Interactive challenge widgets — the visitor is being asked to prove they are
# human, rather than being refused.
#
# `#baxia-punish` is Alibaba's (Daraz, Lazada, AliExpress); it wraps the nc
# slider CAPTCHA. Taken from the live daraz.com.bd wall, where nc.js injects it
# at runtime — the static document only references it from script, so this
# matches the rendered DOM, not the served HTML.
_CHALLENGE_SELECTORS = (
    "#challenge-form",
    "#cf-chl-widget",
    "#challenge-running",
    "#turnstile-wrapper",
    "#baxia-punish",
)

# Alibaba's wall announces itself in the URL rather than in the response: it
# redirects to a punish page served with HTTP 200 and no distinguishing
# headers, so neither the status nor `server` gives it away. Matched on the URL
# because that survives a page whose JS has not run yet.
#
# Measured on daraz.com.bd (2026-08-16): a headless *persistent* context is
# punished on the very first navigation, while a headful one — and the plain
# context replay uses — is let through. That asymmetry is why an agent run
# could not author a site a human could record by hand.
_PUNISH_URL_MARKERS = (
    "/_____tmd_____/punish",
    "x5secdata=",
)

_BLOCK_TITLES = (
    "attention required",
    "just a moment",
    "access denied",
    "sorry, you have been blocked",
)

# Statuses a bot wall answers with. A 404 or a 500 is an ordinary site error and
# must not be reported as a block.
_BLOCK_STATUSES = (403, 429, 503)

_PROBE_JS = """
() => ({
  title: document.title || '',
  block: %s.some(s => document.querySelector(s) !== null),
  challenge: %s.some(s => document.querySelector(s) !== null),
})
""" % (list(_BLOCK_SELECTORS), list(_CHALLENGE_SELECTORS))


@dataclass(frozen=True)
class Block:
    kind: str  # "blocked" | "challenge"
    detail: str

    @property
    def is_challenge(self) -> bool:
        return self.kind == "challenge"

    def message(self) -> str:
        if self.is_challenge:
            return (
                f"This site is challenging automated visits ({self.detail}). "
                "Record it yourself in the browser window and clear the challenge, "
                "then the workflow can be replayed."
            )
        return (
            f"This site is blocking automated visits ({self.detail}). "
            "It cannot be turned into an API automatically."
        )


def from_response(response) -> Block | None:
    """Reads the navigation response. `response` may be None — Playwright
    returns None for same-document navigations."""
    if response is None:
        return None
    try:
        status = response.status
        headers = response.headers
    except Exception:
        return None

    # Set by Cloudflare when it interposes a challenge, whatever the status.
    if headers.get("cf-mitigated"):
        return Block("challenge", f"cf-mitigated: {headers['cf-mitigated']}")

    if status not in _BLOCK_STATUSES:
        return None
    # Require positive evidence it is the edge refusing us, not the site's own
    # 403 (a private page, an expired session) which the caller should surface
    # as an ordinary failure.
    server = (headers.get("server") or "").lower()
    if "cloudflare" not in server and not headers.get("cf-ray"):
        return None
    ray = headers.get("cf-ray")
    detail = f"HTTP {status} from Cloudflare" + (f", ray {ray}" if ray else "")
    return Block("blocked", detail)


def from_url(url: str | None) -> Block | None:
    """Reads the address bar. The only signal Alibaba's wall gives us that does
    not depend on the page's own JS having run."""
    if not url:
        return None
    if any(marker in url for marker in _PUNISH_URL_MARKERS):
        return Block("challenge", "an Alibaba anti-bot page (x5sec punish)")
    return None


async def from_page(page) -> Block | None:
    """Reads the rendered page. Catches the case where the wall is served with
    HTTP 200, and works where no response object is available."""
    try:
        wall = from_url(page.url)
    except Exception:
        wall = None
    if wall is not None:
        return wall

    try:
        probe = await page.evaluate(_PROBE_JS)
    except Exception:
        return None

    title = (probe.get("title") or "").strip()
    if probe.get("challenge"):
        return Block("challenge", "an interactive challenge is on screen")
    # A known block title alone is not enough — a real page could carry one.
    # Cloudflare's own error markup has to be present too.
    if probe.get("block") and any(t in title.lower() for t in _BLOCK_TITLES):
        return Block("blocked", f"block page titled {title!r}")
    return None


async def detect(page, response=None) -> Block | None:
    """Both checks, response first (cheaper and more specific)."""
    return from_response(response) or await from_page(page)
