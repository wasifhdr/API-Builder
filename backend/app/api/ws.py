import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.deps import SESSION_COOKIE
from app.db import async_session
from app.models.workflow import Workflow, WorkflowStatus
from app.redis import redis_client

router = APIRouter()

HEARTBEAT_POLL_SECONDS = 3
HEARTBEAT_LAUNCH_GRACE_SECONDS = 20


@router.websocket("/ws/recordings/{workflow_id}")
async def recording_ws(websocket: WebSocket, workflow_id: uuid.UUID) -> None:
    sid = websocket.cookies.get(SESSION_COOKIE)
    user_id: uuid.UUID | None = None
    if sid:
        user_id_str = await redis_client.hget(f"sess:{sid}", "user_id")
        if user_id_str:
            user_id = uuid.UUID(user_id_str)

    if user_id is None:
        await websocket.close(code=4401)
        return

    async with async_session() as db:
        workflow = await db.get(Workflow, workflow_id)

    if workflow is None or workflow.user_id != user_id:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    evt_channel = f"rec:evt:{workflow_id}"
    cmd_channel = f"rec:cmd:{workflow_id}"
    alive_key = f"rec:alive:{workflow_id}"

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(evt_channel)

    stop = asyncio.Event()

    async def pubsub_to_ws() -> None:
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                await websocket.send_text(message["data"])
        except Exception:
            pass
        finally:
            stop.set()

    async def ws_to_pubsub() -> None:
        try:
            while True:
                data = await websocket.receive_text()
                await redis_client.publish(cmd_channel, data)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            stop.set()

    async def mark_died() -> None:
        await websocket.send_text(json.dumps({"t": "died"}))
        async with async_session() as db:
            wf = await db.get(Workflow, workflow_id)
            if wf is not None and wf.status == WorkflowStatus.RECORDING:
                wf.status = WorkflowStatus.DRAFT
                await db.commit()
        stop.set()

    async def heartbeat_watch() -> None:
        # Checks the heartbeat key's actual current state rather than waiting
        # to observe a "ready" pub/sub event — a client that (re)connects
        # after "ready" was already published (or mid-launch, just before
        # the worker's first heartbeat lands) would otherwise never learn
        # the session is live, since pub/sub doesn't replay history.
        loop = asyncio.get_event_loop()
        launch_deadline = loop.time() + HEARTBEAT_LAUNCH_GRACE_SECONDS

        while not stop.is_set():
            if await redis_client.exists(alive_key):
                await websocket.send_text(json.dumps({"t": "status", "state": "ready"}))
                break
            if loop.time() > launch_deadline:
                await mark_died()
                return
            await asyncio.sleep(1)

        while not stop.is_set():
            if not await redis_client.exists(alive_key):
                await mark_died()
                return
            await asyncio.sleep(HEARTBEAT_POLL_SECONDS)

    tasks = [
        asyncio.create_task(pubsub_to_ws()),
        asyncio.create_task(ws_to_pubsub()),
        asyncio.create_task(heartbeat_watch()),
    ]
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pubsub.unsubscribe(evt_channel)
        await pubsub.aclose()
        try:
            await websocket.close()
        except Exception:
            pass
