import asyncio
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from app.config import settings
from app.core.security import encrypt_bytes
from app.db import async_session
from app.llm.authoring import suggest_extraction_fields, suggest_parameters
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.constants import RECORDED_EVENT_TYPES, VALUE_STEP_TYPES
from app.recorder.extraction import run_extraction
from app.recorder.profiles import get_profile_dir
from app.recorder.schema_infer import infer_schema
from app.recorder.selector_compiler import compile_from_pick, compile_root_from_pick
from app.redis import redis_client

log = logging.getLogger("recorder")

INJECTED_JS_PATH = Path(__file__).resolve().parent / "injected.js"

IDLE_TIMEOUT_SECONDS = 10 * 60
HARD_CAP_SECONDS = 30 * 60
HEARTBEAT_TTL_SECONDS = 15
HEARTBEAT_INTERVAL_SECONDS = 5

VALID_PARAM_TYPES = {"string", "integer", "number", "boolean"}


class RecordingSession:
    def __init__(self, workflow_id: str, user_id: str, rerecord: bool = False):
        self.workflow_id = uuid.UUID(workflow_id)
        self.user_id = uuid.UUID(user_id)
        self.rerecord = rerecord
        self.redis = redis_client
        self.steps: list[dict] = []
        self.parameters: list[dict] = []
        self.extraction: dict = {}
        self.mode = "record"
        self.page: Page | None = None
        self.use_saved_logins = False
        self.captured_storage_state: dict | None = None
        self.final_sample: object | None = None
        self.final_schema: dict | None = None
        self.recorded_viewport: dict | None = None
        self.last_activity = time.monotonic()
        self.started_at = time.monotonic()
        self._stop = asyncio.Event()
        self._save_requested: dict | None = None
        self._cancelled = False
        self._warned_popup = False
        self._warned_iframes = False
        self._authoring_task: asyncio.Task | None = None
        self._last_pick: dict | None = None

    @property
    def evt_channel(self) -> str:
        return f"rec:evt:{self.workflow_id}"

    @property
    def cmd_channel(self) -> str:
        return f"rec:cmd:{self.workflow_id}"

    @property
    def alive_key(self) -> str:
        return f"rec:alive:{self.workflow_id}"

    async def _publish(self, event: dict) -> None:
        await self.redis.publish(self.evt_channel, json.dumps(event))

    async def run(self) -> None:
        async with async_session() as db:
            user = await db.get(User, self.user_id)
            workflow = await db.get(Workflow, self.workflow_id)

        if user is None or workflow is None:
            await self._publish({"t": "error", "message": "workflow or user not found"})
            return

        self.use_saved_logins = bool(user.settings.get("use_saved_logins"))
        channel = "chrome" if user.settings.get("recorder_channel") == "chrome" else None
        profile_dir, is_temp = get_profile_dir(self.user_id, self.use_saved_logins)

        await self._publish({"t": "status", "state": "launching"})

        try:
            async with async_playwright() as pw:
                # no_viewport: otherwise Playwright emulates a fixed 1280×720
                # viewport — the page clips in small windows, ignores resizes,
                # and --start-maximized never takes effect.
                launch_kwargs: dict = {
                    "headless": False,
                    "args": ["--start-maximized"],
                    "no_viewport": True,
                }
                if channel:
                    launch_kwargs["channel"] = channel
                context = await pw.chromium.launch_persistent_context(str(profile_dir), **launch_kwargs)
                try:
                    await self._run_in_context(context, workflow.start_url)
                finally:
                    await context.close()
        finally:
            if is_temp:
                shutil.rmtree(profile_dir, ignore_errors=True)
            await self.redis.delete(self.alive_key)

        await self._finalize()

    async def _run_in_context(self, context: BrowserContext, start_url: str) -> None:
        page = context.pages[0] if context.pages else await context.new_page()
        self.page = page

        # End the session the moment the browser goes away — the user closed
        # the Chromium window, or it crashed. Without this the session lingers
        # (browser dead, nothing to record) until the 10-min idle watchdog,
        # holding the single recording slot (REC_MAX_CONCURRENCY=1) the whole
        # time; every new recording then starves — its job is never read, no
        # heartbeat is published, and the client's WS reports "died" after its
        # 20s grace ("RECORDER CRASHED", 0 steps) until the zombie times out.
        context.on("close", lambda _=None: self._stop.set())

        async def on_event(source, event: dict) -> None:
            await self._handle_page_event(event)

        await context.expose_binding("__abEmit", on_event)
        await context.add_init_script(path=str(INJECTED_JS_PATH))
        context.on("page", lambda new_page: asyncio.create_task(self._handle_new_page(new_page)))

        last_url = start_url

        async def on_frame_navigated(frame) -> None:
            nonlocal last_url
            if frame != page.main_frame or not frame.url or frame.url == "about:blank":
                return
            if frame.url == last_url:
                return
            last_url = frame.url
            if self.steps:  # skip the initial goto — recorded explicitly below
                self._record_step({"type": "goto", "url": frame.url})
                await self._publish({"t": "step_recorded", "step": self.steps[-1]})
            try:
                await page.evaluate("(m) => window.__abSetMode && window.__abSetMode(m)", self.mode)
            except Exception:
                pass
            await self._check_iframes()

        page.on("framenavigated", lambda frame: asyncio.create_task(on_frame_navigated(frame)))

        await page.goto(start_url, wait_until="domcontentloaded")
        self._record_step({"type": "goto", "url": start_url})
        await self._publish({"t": "step_recorded", "step": self.steps[-1]})

        # Subscribe to the command channel *before* announcing "ready" — a
        # command published the instant a client sees "ready" must not be
        # dropped just because the listener task hadn't started yet (Redis
        # pub/sub has no replay/backlog for a subscriber that joins late).
        cmd_pubsub = self.redis.pubsub()
        await cmd_pubsub.subscribe(self.cmd_channel)

        await self._publish({"t": "status", "state": "ready"})
        await self._check_iframes()

        tasks = [
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._watchdog_loop()),
            asyncio.create_task(self._command_loop(cmd_pubsub)),
        ]
        try:
            await self._stop.wait()
        finally:
            all_tasks = list(tasks)
            if self._authoring_task is not None:
                all_tasks.append(self._authoring_task)
            for t in all_tasks:
                t.cancel()
            await asyncio.gather(*all_tasks, return_exceptions=True)

            if not self._cancelled:
                await self._capture_recorded_viewport()
                await self._capture_final_extraction()
                if self.use_saved_logins:
                    try:
                        self.captured_storage_state = await context.storage_state()
                    except Exception:
                        log.exception("failed to capture storage_state")

    async def _handle_new_page(self, new_page: Page) -> None:
        # Multi-tab flows aren't supported (§15) — the worker keeps recording
        # on the original page. Warn once rather than silently ignoring it,
        # since a popup-driven flow otherwise fails mysteriously at replay
        # time with no steps ever recorded for the actual interaction.
        if self._warned_popup:
            return
        self._warned_popup = True
        await self._publish({
            "t": "warning",
            "message": "A new tab or window opened. Multi-tab flows aren't supported yet — "
                       "recording continues on the original page only.",
        })

    async def _check_iframes(self) -> None:
        if self._warned_iframes or self.page is None:
            return
        try:
            frame_count = len(self.page.frames) - 1  # exclude the main frame
        except Exception:
            return
        if frame_count > 0:
            self._warned_iframes = True
            await self._publish({
                "t": "warning",
                "message": "This page has embedded iframes. Elements inside them can't be "
                           "picked or recorded yet.",
            })

    async def _capture_recorded_viewport(self) -> None:
        # With no_viewport the page tracks the real window, so replay can't
        # assume a size. Store what the user actually recorded at — replaying
        # at the same size keeps responsive layouts (and the CSS-path
        # selectors derived from them) consistent between record and replay.
        if self.page is None:
            return
        try:
            size = await self.page.evaluate(
                "() => ({ width: window.innerWidth, height: window.innerHeight })")
            width, height = int(size["width"]), int(size["height"])
        except Exception:
            log.exception("failed to capture recorded viewport")
            return
        if width > 0 and height > 0:
            self.recorded_viewport = {"width": width, "height": height}

    async def _capture_final_extraction(self) -> None:
        config = self.extraction.get("main")
        if not config or self.page is None:
            return
        try:
            self.final_sample = await run_extraction(self.page, config)
            self.final_schema = infer_schema(self.final_sample)
        except Exception:
            log.exception("final extraction failed at save time")

    def _record_step(self, step: dict) -> None:
        step = {"i": len(self.steps), **step}
        self.steps.append(step)
        self.last_activity = time.monotonic()

    async def _handle_page_event(self, event: dict) -> None:
        etype = event.get("type")

        if etype == "pick_result":
            self._last_pick = {
                "pick_id": event.get("pickId"),
                "selectors": event.get("selectors", []),
                "preview": event.get("preview"),
                "generalized": event.get("generalized"),
                "outline": event.get("outline", []),
                "rect": event.get("rect"),
            }
            await self._publish({
                "t": "pick_result",
                "candidate": {
                    "selectors": event.get("selectors", []),
                    "preview": event.get("preview"),
                    "count": event.get("count"),
                    "generalized": event.get("generalized"),
                    "pick_id": event.get("pickId"),
                },
            })
            return

        if etype not in RECORDED_EVENT_TYPES:
            return

        step = {k: v for k, v in event.items() if k != "type"}
        step["type"] = etype
        if etype in VALUE_STEP_TYPES and "value" in step:
            step["value"] = {"literal": step["value"]}
        self._record_step(step)
        await self._publish({"t": "step_recorded", "step": self.steps[-1]})

    async def _heartbeat_loop(self) -> None:
        # A transient Redis blip (e.g. Docker Desktop dropping a pooled
        # connection) must not kill the heartbeat — a dead heartbeat lets the
        # alive-key TTL lapse and the client reports "RECORDER CRASHED" over a
        # still-live session. Swallow and retry on the next beat; the 15s TTL
        # survives two consecutive misses at the 5s interval.
        while True:
            try:
                await self.redis.set(self.alive_key, "1", ex=HEARTBEAT_TTL_SECONDS)
            except Exception:
                log.warning("heartbeat set failed (transient redis error); retrying next beat")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if now - self.started_at > HARD_CAP_SECONDS:
                await self._publish(
                    {"t": "error", "message": "Session hit the 30-minute hard cap and was saved as a draft."})
                self._stop.set()
                return
            if now - self.last_activity > IDLE_TIMEOUT_SECONDS:
                await self._publish(
                    {"t": "error", "message": "Session timed out after 10 minutes of inactivity and was saved as a draft."})
                self._stop.set()
                return

    async def _command_loop(self, pubsub) -> None:
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    cmd = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                await self._handle_command(cmd)
        finally:
            await pubsub.unsubscribe(self.cmd_channel)
            await pubsub.aclose()

    async def _handle_command(self, cmd: dict) -> None:
        ctype = cmd.get("t")
        self.last_activity = time.monotonic()

        if ctype == "set_mode":
            self.mode = cmd.get("mode", "record")
            if self.page is not None:
                try:
                    await self.page.evaluate("(m) => window.__abSetMode && window.__abSetMode(m)", self.mode)
                except Exception:
                    pass
        elif ctype == "undo_step":
            i = cmd.get("i")
            if isinstance(i, int) and 0 <= i < len(self.steps):
                self.steps.pop(i)
                for j, s in enumerate(self.steps):
                    s["i"] = j
                await self._publish({"t": "step_removed", "i": i})
        elif ctype == "bring_to_front":
            if self.page is not None:
                try:
                    await self.page.bring_to_front()
                except Exception:
                    pass
        elif ctype == "mark_param":
            await self._handle_mark_param(cmd)
        elif ctype == "set_extraction":
            config = cmd.get("config")
            if isinstance(config, dict):
                self.extraction["main"] = config
        elif ctype == "test_extraction":
            await self._handle_test_extraction()
        elif ctype == "compile_root":
            await self._handle_compile_root()
        elif ctype == "compile_field":
            await self._handle_compile_field(cmd)
        elif ctype == "suggest_authoring":
            self._start_authoring_task()
        elif ctype == "save":
            self._save_requested = cmd
            self._stop.set()
        elif ctype == "cancel":
            self._cancelled = True
            self._stop.set()
        else:
            log.debug("ignoring command not yet supported: %s", ctype)

    async def _handle_mark_param(self, cmd: dict) -> None:
        step_i = cmd.get("step_i")
        name = cmd.get("name")
        if not isinstance(step_i, int) or not name:
            return
        if not (0 <= step_i < len(self.steps)):
            return
        step = self.steps[step_i]
        if step.get("type") not in VALUE_STEP_TYPES or not isinstance(step.get("value"), dict):
            return

        # type/description are optional — set when accepting an AI suggestion
        # (§ AI-assisted authoring); a bare command behaves exactly as before.
        ptype = cmd.get("type")
        if ptype not in VALID_PARAM_TYPES:
            ptype = "string"
        description = cmd.get("description")
        if not isinstance(description, str):
            description = None

        literal = step["value"].get("literal")
        step["value"] = {"param": name}
        self.parameters = [p for p in self.parameters if p["name"] != name]
        self.parameters.append({
            "name": name,
            "type": ptype,
            "required": True,
            "example": literal,
            "description": description,
            "source_step": step_i,
        })
        await self._publish({"t": "param_marked", "parameter": self.parameters[-1], "step": step})

    async def _handle_test_extraction(self) -> None:
        config = self.extraction.get("main")
        if not config or self.page is None:
            await self._publish({"t": "error", "message": "no extraction configured yet"})
            return
        try:
            sample = await run_extraction(self.page, config)
            schema = infer_schema(sample)
            await self._publish({"t": "extraction_result", "sample": sample, "schema": schema})
        except Exception as exc:
            await self._publish({"t": "error", "message": f"extraction failed: {exc}"})

    async def _handle_compile_root(self) -> None:
        if self._last_pick is None or self.page is None:
            return
        try:
            roots = await compile_root_from_pick(self.page, self._last_pick)
        except Exception as exc:
            log.warning("compile_root failed: %s", exc)
            roots = [self._last_pick.get("generalized") or ""]
        await self._publish({"t": "root_compiled", "roots": [r for r in roots if r]})

    async def _handle_compile_field(self, cmd: dict) -> None:
        if self._last_pick is None or self.page is None:
            return
        name = cmd.get("name") or "field"
        take = cmd.get("take") or "text"
        field = {
            "name": name,
            "description": cmd.get("description"),
            "example": self._last_pick.get("preview"),
            "take": take,
        }
        try:
            selectors = await compile_from_pick(
                self.page,
                self._last_pick,
                mode=cmd.get("mode") or "single",
                root=cmd.get("root"),
                field=field,
            )
        except Exception as exc:
            log.warning("compile_field failed: %s", exc)
            selectors = list(self._last_pick.get("selectors") or [])
        await self._publish({
            "t": "field_compiled",
            "field": {
                "name": name,
                "description": cmd.get("description"),
                "take": take,
                "example": self._last_pick.get("preview"),
                "selectors": selectors,
            },
        })

    def _start_authoring_task(self) -> None:
        if self._authoring_task is not None and not self._authoring_task.done():
            return  # a suggestion request is already in flight — ignore repeats
        self._authoring_task = asyncio.create_task(self._run_authoring_suggestions())

    async def _run_authoring_suggestions(self) -> None:
        """Suggests parameters and extraction field names via the LLM
        (§ AI-assisted authoring, docs/AI_AUTHORING_PLAN.md). Every suggestion
        is advisory: acceptance flows through the existing mark_param /
        set_extraction commands, never a second write path. This must never
        propagate an exception — a failed suggestion degrades to "no
        suggestions", not a crashed recording session."""
        if not settings.llm_enabled:
            await self._publish({"t": "error", "message": "AI suggestions are disabled on this server."})
            return

        parameters: list[dict] = []
        extraction_fields: list[dict] = []

        try:
            parameters = await suggest_parameters(self.steps)
        except Exception as exc:
            log.exception("parameter suggestion failed")
            await self._publish({"t": "error", "message": f"Parameter suggestions failed: {exc}"})

        try:
            config = self.extraction.get("main")
            if config and self.page is not None:
                sample = await run_extraction(self.page, config)
                extraction_fields = await suggest_extraction_fields(config, sample)
        except Exception as exc:
            log.exception("extraction field suggestion failed")
            await self._publish({"t": "error", "message": f"Extraction field suggestions failed: {exc}"})

        await self._publish({
            "t": "authoring_suggestions",
            "parameters": parameters,
            "extraction_fields": extraction_fields,
        })

    async def _finalize(self) -> None:
        async with async_session() as db:
            workflow = await db.get(Workflow, self.workflow_id)
            if workflow is None:
                return
            if self._cancelled:
                # A cancelled/timed-out re-record must not archive a workflow
                # that already backs a live API, but its status must still
                # reflect whether it actually has extraction (the cancelled
                # session did not overwrite it) — a rootless/empty re-record
                # can't masquerade as READY and get synced live. A cancelled
                # *fresh* recording is a throwaway draft, so it still archives.
                workflow.status = (
                    (WorkflowStatus.READY if workflow.extraction.get("main") else WorkflowStatus.DRAFT)
                    if self.rerecord
                    else WorkflowStatus.ARCHIVED
                )
            else:
                if self._save_requested and self._save_requested.get("name"):
                    workflow.name = self._save_requested["name"]
                if self.extraction.get("main") and not any(s.get("type") == "extract" for s in self.steps):
                    self._record_step({"type": "extract", "ref": "main"})
                workflow.steps = self.steps
                workflow.parameters = self.parameters
                workflow.extraction = self.extraction
                if self.final_sample is not None:
                    workflow.sample_output = self.final_sample
                if self.final_schema is not None:
                    workflow.output_schema = self.final_schema
                if self.captured_storage_state is not None:
                    blob = json.dumps(self.captured_storage_state).encode()
                    workflow.auth_state_encrypted = encrypt_bytes(blob)
                if self.recorded_viewport is not None:
                    workflow.browser_settings = {
                        **workflow.browser_settings, "viewport": self.recorded_viewport}
                workflow.status = WorkflowStatus.READY if self.extraction.get("main") else WorkflowStatus.DRAFT
            await db.commit()

        await self._publish({"t": "status", "state": "closed"})
        if not self._cancelled:
            await self._publish({"t": "saved"})
