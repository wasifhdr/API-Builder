"""End-to-end smoke test for the whole pipeline, driven for real — no mocks.

Records a workflow against the local static fixture site by speaking the
actual WS recording protocol (the same one the frontend uses), publishes it,
executes it through the real public /v1/run endpoint, and asserts the
extracted JSON matches the fixture page. Requires Postgres/Redis, the
backend (uvicorn), and the worker to already be running — this drives the
real system rather than starting one.

Bypasses Google OAuth by seeding a session directly (same technique used
throughout manual testing of this project): a real user row + a real
`sess:{sid}` Redis hash, which is indistinguishable to the app from a
session created via the real login flow.

Usage: uv run python -m scripts.e2e_smoke   (from backend/)
"""

import asyncio
import http.server
import json
import secrets
import sys
import threading
import uuid
from functools import partial
from pathlib import Path

import httpx
import websockets
from sqlalchemy import text

from app.db import async_session
from app.models.user import User
from app.redis import redis_client

BACKEND_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000"
FIXTURE_SITE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "site"
FIXTURE_SITE_PORT = 8322

EXTRACTION_CONFIG = {
    "mode": "list",
    "root": ".book-item",
    "fields": [
        {"name": "title", "selector": ".book-title", "take": "text"},
        {"name": "price", "selector": ".book-price", "take": "text", "transform": "number"},
    ],
}
EXPECTED_BOOKS = [
    {"title": "Physics 101", "price": 350},
    {"title": "Chemistry Basics", "price": 420},
    {"title": "Advanced Biology", "price": 500},
]

WS_TIMEOUT_SECONDS = 30
REPLAY_TIMEOUT_SECONDS = 60


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def start_fixture_site() -> http.server.ThreadingHTTPServer:
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE_SITE_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", FIXTURE_SITE_PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


async def make_session() -> tuple[uuid.UUID, str]:
    async with async_session() as db:
        user = User(google_sub=f"e2e-smoke-{uuid.uuid4()}", email=f"e2e-smoke-{uuid.uuid4()}@example.com", name="E2E Smoke")
        db.add(user)
        await db.commit()
        await db.refresh(user)

    sid = secrets.token_urlsafe(24)
    await redis_client.hset(f"sess:{sid}", mapping={"user_id": str(user.id)})
    await redis_client.expire(f"sess:{sid}", 3600)
    return user.id, sid


async def wait_for(ws, predicate, timeout: float = WS_TIMEOUT_SECONDS) -> dict:
    async def _wait():
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)
            log(f"ws <- {msg}")
            if predicate(msg):
                return msg

    return await asyncio.wait_for(_wait(), timeout=timeout)


async def cleanup(user_id: uuid.UUID, sid: str) -> None:
    # Raw SQL, not db.delete(user): the ORM's User.apis/User.workflows
    # relationships aren't declared passive_deletes=True, so an ORM-level
    # delete tries to null out custom_apis.owner_id (NOT NULL) instead of
    # deferring to the real ON DELETE CASCADE on that FK. A plain DELETE
    # lets Postgres cascade it correctly, same as manual cleanup elsewhere
    # in this project.
    async with async_session() as db:
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})
        await db.commit()
    await redis_client.delete(f"sess:{sid}")


async def run() -> bool:
    log("starting fixture site server")
    httpd = start_fixture_site()
    user_id: uuid.UUID | None = None
    sid: str | None = None
    try:
        user_id, sid = await make_session()
        log(f"created test user {user_id}")
        cookies = {"ab_session": sid}

        async with httpx.AsyncClient(base_url=BACKEND_URL, cookies=cookies, timeout=30.0) as client:
            resp = await client.post(
                "/api/recordings",
                json={"name": "E2E Smoke Test", "start_url": f"http://127.0.0.1:{FIXTURE_SITE_PORT}/index.html"},
            )
            resp.raise_for_status()
            workflow_id = resp.json()["workflow_id"]
            log(f"created workflow {workflow_id}")

            ws_uri = f"{WS_URL}/api/ws/recordings/{workflow_id}"
            async with websockets.connect(ws_uri, additional_headers={"Cookie": f"ab_session={sid}"}) as ws:
                await wait_for(ws, lambda m: m.get("t") == "status" and m.get("state") == "ready")
                log("recorder ready — sending extraction config")

                await ws.send(json.dumps({"t": "set_extraction", "config": EXTRACTION_CONFIG}))
                await ws.send(json.dumps({"t": "save", "name": "E2E Smoke Test"}))
                await wait_for(ws, lambda m: m.get("t") == "saved")
                log("workflow saved")

            resp = await client.post(f"/api/workflows/{workflow_id}/publish")
            resp.raise_for_status()
            api = resp.json()
            slug = api["slug"]
            log(f"published as slug={slug}")

            resp = await client.post("/api/keys", json={"label": "e2e-smoke"})
            resp.raise_for_status()
            api_key = resp.json()["api_key"]

        async with httpx.AsyncClient(timeout=REPLAY_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{BACKEND_URL}/v1/run/{slug}", headers={"X-API-Key": api_key})
            resp.raise_for_status()
            data = resp.json()["data"]
            log(f"execution returned: {data}")

        if data != EXPECTED_BOOKS:
            log(f"FAIL: expected {EXPECTED_BOOKS}, got {data}")
            return False

        log("PASS: extracted data matches the fixture page exactly")
        return True
    finally:
        httpd.shutdown()
        if user_id is not None and sid is not None:
            await cleanup(user_id, sid)
            log("cleaned up test user and session")


def main() -> int:
    ok = asyncio.run(run())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
