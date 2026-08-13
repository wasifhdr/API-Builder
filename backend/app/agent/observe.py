import base64
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import ElementHandle, Page

OBSERVE_JS_PATH = Path(__file__).resolve().parent / "observe.js"
_REF_RE = re.compile(r"^ref_(\d+)$")


class RefNotFound(Exception):
    """A ref the model named is not in the current observation."""


@dataclass(frozen=True)
class Observation:
    tree: str
    ref_count: int
    screenshot_b64: str
    url: str
    title: str


async def observe(page: Page, with_screenshot: bool = True) -> Observation:
    """Snapshots the page for the agent: an interactive-element listing, a
    sample of repeated content blocks, and (optionally) a screenshot.

    The listing is curated rather than a raw DOM dump — burying the real
    controls in inline SVG and framework hashes makes the model worse at
    finding them, not better.
    """
    raw = await page.evaluate(OBSERVE_JS_PATH.read_text(encoding="utf-8"))

    sections = [f"URL: {raw['url']}", f"TITLE: {raw['title']}"]
    if raw["interactive"]:
        sections.append("INTERACTIVE ELEMENTS:\n" + raw["interactive"])
    if raw["blocks"]:
        sections.append("CONTENT BLOCKS (sampled):\n" + raw["blocks"])

    screenshot_b64 = ""
    if with_screenshot:
        screenshot_b64 = base64.b64encode(await page.screenshot(type="png")).decode()

    return Observation(
        tree="\n\n".join(sections),
        ref_count=raw["refCount"],
        screenshot_b64=screenshot_b64,
        url=raw["url"],
        title=raw["title"],
    )


async def resolve_ref(page: Page, ref: str) -> ElementHandle:
    """Turns a ref_N handle from the last observation back into a live element.

    Refs live in a JS array, so they are invalidated by navigation — the caller
    must re-observe after any page load before acting again.
    """
    match = _REF_RE.match(ref or "")
    if match is None:
        raise RefNotFound(f"malformed ref {ref!r}")

    handle = await page.evaluate_handle(
        "i => (window.__abRefs || [])[i] || null", int(match.group(1))
    )
    element = handle.as_element()
    if element is None:
        raise RefNotFound(
            f"{ref} is not on the current page — re-observe before acting"
        )
    return element
