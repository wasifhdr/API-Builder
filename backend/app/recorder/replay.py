import uuid
from typing import Any

from playwright.async_api import Page, async_playwright

from app.config import settings
from app.recorder.extraction import run_extraction
from app.recorder.llm_extract import llm_fill_missing

# Per-candidate wait budgets, tried in order until one selector matches —
# absorbs the small selector drift that's common between recording and
# replay without needing a full re-record.
SELECTOR_ATTEMPT_TIMEOUTS_MS = [10_000, 5_000, 5_000]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


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


async def _locate(page: Page, selectors: list[str]):
    last_exc: Exception | None = None
    for selector, timeout_ms in zip(selectors, SELECTOR_ATTEMPT_TIMEOUTS_MS):
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=timeout_ms)
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
        selector = config.get("root")
    else:
        fields = config.get("fields") or []
        selector = fields[0].get("selector") if fields else None
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
) -> dict[str, Any]:
    steps = workflow_snapshot.get("steps", [])
    extraction = workflow_snapshot.get("extraction", {})
    data: Any = None

    # Per-run override (owner's UI toggle) wins; otherwise fall back to the
    # process-level default from config/.env.
    launch_headless = settings.replay_headless if headless is None else headless

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=launch_headless,
            slow_mo=settings.replay_slow_mo_ms,
            args=["--disable-gpu"],
        )
        context_kwargs: dict = {
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": _replay_viewport(workflow_snapshot),
        }
        if storage_state is not None:
            context_kwargs["storage_state"] = storage_state
        context = await browser.new_context(**context_kwargs)
        context.set_default_timeout(10_000)
        page = await context.new_page()

        try:
            for step in steps:
                stype = step.get("type")

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
                    config = extraction.get(step.get("ref", "main"))
                    if config:
                        await _wait_for_extraction_ready(page, config)
                        data = await run_extraction(page, config)
                        data = await llm_fill_missing(page, config, data)
        except Exception as exc:
            artifact_path = await _dump_failure_artifacts(page, execution_id)
            await context.close()
            await browser.close()
            raise ReplayError(str(exc), artifact_path=artifact_path) from exc
        else:
            await context.close()
            await browser.close()

    return {"data": data}
