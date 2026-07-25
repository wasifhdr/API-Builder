import asyncio
import contextlib
import logging
import uuid
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from app.config import settings
from app.db import async_session
from app.recorder.extraction import run_extraction
from app.recorder.llm_extract import llm_fill_missing, semantic_extract
from app.recorder.selector_cache import read_cache, upsert_cache
from app.recorder import selector_compiler
from app.recorder import stealth

log = logging.getLogger("recorder")

# Per-candidate wait budgets, tried in order until one selector matches —
# absorbs the small selector drift that's common between recording and
# replay without needing a full re-record.
SELECTOR_ATTEMPT_TIMEOUTS_MS = [10_000, 5_000, 5_000]

# An ambiguous selector is scanned for its first visible match rather than
# blindly bound to document order. The cap keeps a pathologically broad selector
# (hundreds of matches) from burning the whole budget on visibility checks.
MAX_SELECTOR_MATCHES_SCANNED = 30
SELECTOR_VISIBILITY_POLL_MS = 250

# Fixed dwell after every page load (initial navigation and any load triggered
# by a click/press) before we begin interacting with the new page. Gives
# heavy/SPA pages time to settle so selectors resolve against the final DOM.
POST_NAV_WAIT_MS = 5_000

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}

# Steady the fingerprint: a locale/timezone that match a normal desktop Chrome
# (and the deployment's Asia/Dhaka clock) rather than the context defaults,
# which detectors flag when they disagree with the IP or each other.
DEFAULT_LOCALE = "en-US"
DEFAULT_TIMEZONE = "Asia/Dhaka"

# Chromium single-instance-locks a profile directory, so two replays reusing the
# same owner's warmed profile can't run at once. Serialize per user_id. The
# worker is the sole Playwright owner (single process), so an in-process lock is
# sufficient — no cross-process file lock needed.
_profile_locks: dict[str, asyncio.Lock] = {}


def _profile_lock(user_id: uuid.UUID) -> asyncio.Lock:
    key = str(user_id)
    lock = _profile_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _profile_locks[key] = lock
    return lock


async def _open_context(
    pw,
    *,
    headless: bool,
    viewport: dict,
    storage_state: dict | None,
    profile_dir,
) -> tuple[Any, BrowserContext]:
    """Open the replay browser context. Returns (browser, context); `browser` is
    None for the persistent-profile path (launch_persistent_context yields the
    context directly). Both paths carry the stealth launch args and a
    human-looking UA/locale/timezone; the caller injects STEALTH_INIT_JS."""
    args = stealth.launch_args(headless)
    if profile_dir is not None:
        # Warm-profile path: reuse the owner's recorder profile (cookies,
        # history, consistent fingerprint) and overlay the workflow's captured
        # auth cookies on top so replay authenticates as it did at record time.
        context = await pw.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            slow_mo=settings.replay_slow_mo_ms,
            args=args,
            user_agent=DEFAULT_USER_AGENT,
            viewport=viewport,
            locale=DEFAULT_LOCALE,
            timezone_id=DEFAULT_TIMEZONE,
        )
        cookies = (storage_state or {}).get("cookies")
        if cookies:
            try:
                await context.add_cookies(cookies)
            except Exception as exc:
                log.warning("failed to overlay auth cookies on profile: %s", exc)
        return None, context

    browser = await pw.chromium.launch(headless=headless, slow_mo=settings.replay_slow_mo_ms, args=args)
    context_kwargs: dict = {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": viewport,
        "locale": DEFAULT_LOCALE,
        "timezone_id": DEFAULT_TIMEZONE,
    }
    if storage_state is not None:
        context_kwargs["storage_state"] = storage_state
    context = await browser.new_context(**context_kwargs)
    return browser, context


def _replay_viewport(workflow_snapshot: dict) -> dict:
    # Recording captures the real window size (see RecordingSession); replaying
    # at that size keeps responsive layouts — and the selectors recorded
    # against them — consistent. Older workflows without it get the default.
    viewport = (workflow_snapshot.get("browser_settings") or {}).get("viewport") or {}
    try:
        width, height = int(viewport["width"]), int(viewport["height"])
    except (KeyError, TypeError, ValueError):
        return DEFAULT_VIEWPORT
    if not (200 <= width <= 3840 and 200 <= height <= 2160):
        return DEFAULT_VIEWPORT
    return {"width": width, "height": height}


class ReplayError(Exception):
    def __init__(self, message: str, artifact_path: str | None = None):
        super().__init__(message)
        self.artifact_path = artifact_path


def _resolve_value(value: dict | None, params: dict) -> str:
    if not value:
        return ""
    if "literal" in value:
        return value["literal"]
    if "param" in value:
        return str(params.get(value["param"], ""))
    return ""


def _merge_extraction(selector_data: Any, llm_data: Any) -> Any:
    # LLM owns text/number fields (overlaid on top); selectors own attr:/html
    # fields (kept from selector_data). Shapes match: both a single dict, or
    # both a list of dicts over the same root.
    if isinstance(llm_data, dict) and isinstance(selector_data, dict):
        return {**selector_data, **llm_data}
    if isinstance(llm_data, list) and isinstance(selector_data, list):
        n = max(len(selector_data), len(llm_data))
        return [
            {**(selector_data[i] if i < len(selector_data) else {}),
             **(llm_data[i] if i < len(llm_data) else {})}
            for i in range(n)
        ]
    # Shape mismatch can't occur — both paths are driven by config["mode"];
    # prefer LLM if it ever does.
    return llm_data


def _null_fields(row: dict, fields: list[dict]) -> list[dict]:
    return [f for f in fields if row.get(f["name"]) is None]


async def _extract_compiled(
    page: Page, config: dict, workflow_id: uuid.UUID | None, ref: str
) -> Any:
    """Deterministic ranked-selector extraction, with per-field LLM self-heal for
    fields that come back null. Healed selectors are persisted to the selector
    cache (when workflow_id is known) so the next call is deterministic again.
    Never raises."""
    fields = config.get("fields") or []
    mode = config.get("mode", "single")
    root = config.get("roots")[0] if config.get("roots") else config.get("root")

    # 1. Overlay cached (previously-healed) selectors on top of the authored ones.
    if workflow_id is not None:
        try:
            async with async_session() as db:
                cached = await read_cache(db, workflow_id)
            if cached:
                config = _apply_cache_overlay(config, cached, ref)
        except Exception as exc:
            log.warning("selector cache read failed: %s", exc)

    # 2. Deterministic extraction.
    data = await run_extraction(page, config)

    # 3. Self-heal null fields (single: whole dict; list: per row is too costly,
    #    so heal at the field level using the first row as the probe).
    if mode == "single" and isinstance(data, dict):
        for field in _null_fields(data, fields):
            healed = await selector_compiler.reheal(page, mode="single", root=None, field=field)
            if not healed:
                continue
            value = await run_extraction(
                page, {"mode": "single", "fields": [{**field, "selectors": healed}]}
            )
            if value.get(field["name"]) is not None:
                data[field["name"]] = value[field["name"]]
                if workflow_id is not None:
                    await _persist_heal(workflow_id, ref, field["name"], healed)

    elif mode == "list" and isinstance(data, list) and data:
        # Only heal fields null in EVERY row (a broken selector), not fields
        # legitimately absent from some rows.
        broken = [f for f in fields if all(r.get(f["name"]) is None for r in data)]
        for field in broken:
            healed = await selector_compiler.reheal(page, mode="list", root=root, field=field)
            if not healed:
                continue
            merged_field = {**field, "selectors": healed}
            rows = await run_extraction(
                page, {"mode": "list", "roots": config.get("roots") or ([root] if root else []),
                        "root": root, "fields": [merged_field]}
            )
            filled = False
            for i, r in enumerate(rows):
                if i < len(data) and r.get(field["name"]) is not None:
                    data[i][field["name"]] = r[field["name"]]
                    filled = True
            if filled and workflow_id is not None:
                await _persist_heal(workflow_id, ref, field["name"], healed)

    # 4. Last-resort value-extraction floor for anything STILL null. Overlaid
    #    null-safe (never overwrites a value a selector already produced), since
    #    semantic_extract returns null for text-ineligible/absent fields.
    if _has_null(data, fields):
        floor_config = config if mode != "list" else {**config, "root": config.get("root") or root}
        floor = await semantic_extract(page, floor_config)
        if isinstance(floor, dict) and isinstance(data, dict):
            for k, v in floor.items():
                if data.get(k) is None and v is not None:
                    data[k] = v
        elif isinstance(floor, list) and isinstance(data, list):
            for i, frow in enumerate(floor):
                if i < len(data) and isinstance(frow, dict):
                    for k, v in frow.items():
                        if data[i].get(k) is None and v is not None:
                            data[i][k] = v
    return data


def _has_null(data: Any, fields: list[dict]) -> bool:
    names = [f["name"] for f in fields]
    if isinstance(data, dict):
        return any(data.get(n) is None for n in names)
    if isinstance(data, list):
        return any(row.get(n) is None for row in data for n in names)
    return False


def _apply_cache_overlay(config: dict, cached: dict, ref: str) -> dict:
    fields = []
    for f in config.get("fields") or []:
        key = (ref, f["name"])
        if key in cached:
            fields.append({**f, "selectors": cached[key]})
        else:
            fields.append(f)
    return {**config, "fields": fields}


async def _persist_heal(workflow_id: uuid.UUID, ref: str, field_name: str, selectors: list[str]) -> None:
    try:
        async with async_session() as db:
            await upsert_cache(db, workflow_id, ref, field_name, selectors)
    except Exception as exc:
        log.warning("selector cache upsert failed: %s", exc)


async def _first_visible(page: Page, selector: str, timeout_ms: int):
    """Return the first *visible* node a selector matches, or None on timeout.

    Recorded selectors aren't always unique — a positional css-path fallback can
    match dozens of nodes on a real page. Playwright's `.first` binds by
    document order, which routinely lands on a hidden copy inside a collapsed
    nav drawer or an off-screen mobile layout; waiting for that to become
    visible then times out while the element the user actually clicked sits
    visible further down the page. Scan the matches instead and take the first
    one that's really visible. Polling doubles as the wait for late-rendering
    content, so this keeps the old behaviour when the selector is unique.
    """
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    locator = page.locator(selector)
    while True:
        count = min(await locator.count(), MAX_SELECTOR_MATCHES_SCANNED)
        for i in range(count):
            candidate = locator.nth(i)
            if await candidate.is_visible():
                if i:
                    log.info("selector %r matched %d nodes; using visible #%d", selector, count, i)
                return candidate
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await page.wait_for_timeout(SELECTOR_VISIBILITY_POLL_MS)


async def _locate(page: Page, selectors: list[str]):
    last_exc: Exception | None = None
    for selector, timeout_ms in zip(selectors, SELECTOR_ATTEMPT_TIMEOUTS_MS):
        try:
            locator = await _first_visible(page, selector, timeout_ms)
            if locator is not None:
                return locator
        except Exception as exc:
            last_exc = exc
            continue
    raise ReplayError(f"none of the candidate selectors matched: {selectors}") from last_exc


async def _dump_failure_artifacts(page: Page, execution_id: uuid.UUID) -> str:
    failures_dir = settings.failures_path / str(execution_id)
    failures_dir.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(failures_dir / "screenshot.png"))
    except Exception:
        pass
    try:
        html = await page.content()
        (failures_dir / "page.html").write_text(html, encoding="utf-8")
    except Exception:
        pass
    return str(failures_dir)


async def _wait_for_extraction_ready(page: Page, config: dict) -> None:
    # SPA content (e.g. Canvas's React app) usually renders after
    # domcontentloaded, so extracting immediately races an empty DOM and
    # silently returns nothing. Wait for the target node to attach first.
    # A genuinely-empty result just eats the timeout, then extracts [] anyway.
    if config.get("mode") == "list":
        roots = config.get("roots") or ([config.get("root")] if config.get("root") else [])
        selector = roots[0] if roots else None
    else:
        fields = config.get("fields") or []
        first = fields[0] if fields else None
        selector = None
        if first:
            selector = (first.get("selectors") or [None])[0] or first.get("selector")
    if not selector:
        return
    try:
        await page.locator(selector).first.wait_for(state="attached", timeout=15_000)
    except Exception:
        pass


async def replay_workflow(
    workflow_snapshot: dict,
    params: dict,
    storage_state: dict | None,
    execution_id: uuid.UUID,
    headless: bool | None = None,
    workflow_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    steps = workflow_snapshot.get("steps", [])
    extraction = workflow_snapshot.get("extraction", {})
    data: Any = None

    # Per-run override (owner's UI toggle) wins; otherwise fall back to the
    # process-level default from config/.env.
    launch_headless = settings.replay_headless if headless is None else headless

    # Warm-profile replay: reuse the owner's persistent recorder profile so the
    # visit carries real browsing reputation and a consistent fingerprint,
    # instead of the pristine, obviously-automated context that gets challenged.
    # Only when enabled AND the profile actually exists (owner recorded with
    # saved logins on). Serialized per user (Chromium locks the profile dir).
    profile_dir = None
    if user_id is not None and settings.replay_use_profile:
        candidate = settings.profiles_path / str(user_id)
        if candidate.exists():
            profile_dir = candidate
    lock = _profile_lock(user_id) if (profile_dir is not None and user_id is not None) else contextlib.nullcontext()

    async with lock, async_playwright() as pw:
        browser, context = await _open_context(
            pw,
            headless=launch_headless,
            viewport=_replay_viewport(workflow_snapshot),
            storage_state=storage_state,
            profile_dir=profile_dir,
        )
        context.set_default_timeout(10_000)
        # Injected before any site JS runs on every page — hides the automation
        # tells (navigator.webdriver, empty plugins, headless WebGL) that raise
        # bot-detection challenges. Applies to both launch paths.
        await context.add_init_script(stealth.STEALTH_INIT_JS)
        page = context.pages[0] if context.pages else await context.new_page()

        # Track main-frame navigations so we can dwell POST_NAV_WAIT_MS after
        # every page load — the initial goto and any load a click/press kicks
        # off — BEFORE we begin the next interaction step. The dwell runs ahead
        # of _locate so late-rendering elements get the settle time before the
        # visibility wait even starts (they were failing "none of the candidate
        # selectors matched" because _locate gave up before they appeared).
        nav = {"pending": False}

        def _on_framenavigated(frame) -> None:
            if frame.parent_frame is None:  # main frame only
                nav["pending"] = True

        page.on("framenavigated", _on_framenavigated)

        async def _settle_after_nav() -> None:
            if nav["pending"]:
                nav["pending"] = False
                await page.wait_for_timeout(POST_NAV_WAIT_MS)

        try:
            for step in steps:
                stype = step.get("type")

                # Dwell after any page load before beginning the next
                # interaction, so late-rendering content has settled before we
                # wait for / act on it. (goto does its own load wait; the dwell
                # for it lands here, ahead of the first interaction.)
                if stype != "goto":
                    await _settle_after_nav()

                if stype == "goto":
                    await page.goto(step["url"], wait_until="domcontentloaded")
                elif stype == "click":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.click()
                elif stype == "fill":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.fill(_resolve_value(step.get("value"), params))
                elif stype == "press":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.press(step["key"])
                elif stype == "select_option":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.select_option(value=_resolve_value(step.get("value"), params))
                elif stype == "wait_for":
                    for selector in step.get("selectors", []):
                        try:
                            await page.wait_for_selector(
                                selector,
                                state=step.get("state", "visible"),
                                timeout=step.get("timeout_ms", 15_000),
                            )
                            break
                        except Exception:
                            continue
                elif stype == "scroll_page":
                    for _ in range(step.get("times", 1)):
                        await page.mouse.wheel(0, 2000)
                        await page.wait_for_timeout(300)
                elif stype == "extract":
                    ref = step.get("ref", "main")
                    config = extraction.get(ref)
                    if config:
                        await _wait_for_extraction_ready(page, config)
                        if config.get("engine") == "compiled":
                            data = await _extract_compiled(page, config, workflow_id, ref)
                        elif config.get("engine") == "llm":
                            llm_data = await semantic_extract(page, config)
                            if llm_data is None:
                                data = await run_extraction(page, config)
                            else:
                                selector_data = await run_extraction(page, config)
                                data = _merge_extraction(selector_data, llm_data)
                        else:
                            data = await run_extraction(page, config)
                            data = await llm_fill_missing(page, config, data)
        except Exception as exc:
            artifact_path = await _dump_failure_artifacts(page, execution_id)
            await context.close()
            if browser is not None:
                await browser.close()
            raise ReplayError(str(exc), artifact_path=artifact_path) from exc
        else:
            await context.close()
            if browser is not None:
                await browser.close()

    return {"data": data}
