import asyncio
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

from app.db import async_session
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStatus
from app.recorder.profiles import get_profile_dir
from app.redis import redis_client

log = logging.getLogger("recorder")

INJECTED_JS_PATH = Path(__file__).resolve().parent / "injected.js"

IDLE_TIMEOUT_SECONDS = 10 * 60
HARD_CAP_SECONDS = 30 * 60
HEARTBEAT_TTL_SECONDS = 15
HEARTBEAT_INTERVAL_SECONDS = 5

RECORDED_EVENT_TYPES = {"click", "fill", "press", "select_option"}


class RecordingSession:
    def __init__(self, workflow_id: str, user_id: str):
        self.workflow_id = uuid.UUID(workflow_id)
        self.user_id = uuid.UUID(user_id)
        self.redis = redis_client
        self.steps: list[dict] = []
        self.mode = "record"
        self.page: Page | None = None
        self.last_activity = time.monotonic()
        self.started_at = time.monotonic()
        self._stop = asyncio.Event()
        self._save_requested: dict | None = None
        self._cancelled = False

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

        use_saved_logins = bool(user.settings.get("use_saved_logins"))
        channel = "chrome" if user.settings.get("recorder_channel") == "chrome" else None
        profile_dir, is_temp = get_profile_dir(self.user_id, use_saved_logins)

        await self._publish({"t": "status", "state": "launching"})

        try:
            async with async_playwright() as pw:
                launch_kwargs: dict = {"headless": False, "args": ["--start-maximized"]}
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

        async def on_event(source, event: dict) -> None:
            await self._handle_page_event(event)

        await context.expose_binding("__abEmit", on_event)
        await context.add_init_script(path=str(INJECTED_JS_PATH))

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

        page.on("framenavigated", lambda frame: asyncio.create_task(on_frame_navigated(frame)))

        await page.goto(start_url, wait_until="domcontentloaded")
        self._record_step({"type": "goto", "url": start_url})
        await self._publish({"t": "step_recorded", "step": self.steps[-1]})
        await self._publish({"t": "status", "state": "ready"})

        tasks = [
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._watchdog_loop()),
            asyncio.create_task(self._command_loop()),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _record_step(self, step: dict) -> None:
        step = {"i": len(self.steps), **step}
        self.steps.append(step)
        self.last_activity = time.monotonic()

    async def _handle_page_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype not in RECORDED_EVENT_TYPES:
            return
        step = {k: v for k, v in event.items() if k != "type"}
        step["type"] = etype
        self._record_step(step)
        await self._publish({"t": "step_recorded", "step": self.steps[-1]})

    async def _heartbeat_loop(self) -> None:
        while True:
            await self.redis.set(self.alive_key, "1", ex=HEARTBEAT_TTL_SECONDS)
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

    async def _command_loop(self) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.cmd_channel)
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
        elif ctype == "save":
            self._save_requested = cmd
            self._stop.set()
        elif ctype == "cancel":
            self._cancelled = True
            self._stop.set()
        else:
            log.debug("ignoring command not yet supported: %s", ctype)

    async def _finalize(self) -> None:
        async with async_session() as db:
            workflow = await db.get(Workflow, self.workflow_id)
            if workflow is None:
                return
            if self._cancelled:
                workflow.status = WorkflowStatus.ARCHIVED
            else:
                if self._save_requested and self._save_requested.get("name"):
                    workflow.name = self._save_requested["name"]
                workflow.steps = self.steps
                workflow.status = WorkflowStatus.DRAFT
            await db.commit()

        await self._publish({"t": "status", "state": "closed"})
        if not self._cancelled:
            await self._publish({"t": "saved"})
