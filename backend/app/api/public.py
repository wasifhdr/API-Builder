import asyncio
import hashlib
import json
import time
import uuid

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import settings
from app.core.deps import SESSION_COOKIE
from app.db import async_session
from app.models.api import ApiKey, CustomApi
from app.models.execution import ApiExecution, ExecutionStatus
from app.redis import redis_client
from app.services.param_coercion import ParamCoercionError, coerce_params

RATE_LIMIT_PER_MINUTE = 60
RATE_LIMIT_WINDOW_SECONDS = 120

public_app = FastAPI(title="API Builder — Public API")
public_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _authenticate_key(x_api_key: str | None) -> ApiKey:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing X-API-Key header")
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    async with async_session() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
        key = result.scalar_one_or_none()
    if key is None or key.revoked_at is not None:
        raise HTTPException(status_code=401, detail="invalid or revoked API key")
    return key


async def _check_rate_limit(key_id: uuid.UUID) -> None:
    minute = int(time.time() // 60)
    rl_key = f"rl:key:{key_id}:{minute}"
    count = await redis_client.incr(rl_key)
    await redis_client.expire(rl_key, RATE_LIMIT_WINDOW_SECONDS)
    if count > RATE_LIMIT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def _cache_key(api_id: uuid.UUID, params: dict) -> str:
    sorted_params = json.dumps(params, sort_keys=True)
    digest = hashlib.sha256(sorted_params.encode()).hexdigest()
    return f"cache:exec:{api_id}:{digest}"


async def _next_pubsub_message(pubsub) -> None:
    async for message in pubsub.listen():
        if message["type"] == "message":
            return


@public_app.get("/run/{slug}")
async def run_api(
    slug: str,
    request: Request,
    x_api_key: str | None = Header(default=None),
    prefer: str | None = Header(default=None),
) -> JSONResponse:
    key = await _authenticate_key(x_api_key)
    await _check_rate_limit(key.id)

    async with async_session() as db:
        result = await db.execute(select(CustomApi).where(CustomApi.slug == slug))
        api = result.scalar_one_or_none()
        if api is None:
            raise HTTPException(status_code=404, detail="api not found")
        if not api.is_active:
            raise HTTPException(status_code=403, detail="api is disabled")

        if api.owner_id != key.user_id:
            # Sharing/grants land in Phase 8 — for now only the owner can call
            # their own (private-by-default) API.
            raise HTTPException(status_code=403, detail="no access to this api")

        parameters = api.workflow_snapshot.get("parameters", [])
        try:
            params = coerce_params(parameters, dict(request.query_params))
        except ParamCoercionError as exc:
            raise HTTPException(status_code=422, detail=exc.errors) from exc

        cache_key = _cache_key(api.id, params) if api.cache_ttl_seconds > 0 else None
        if cache_key:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return JSONResponse({"data": json.loads(cached), "meta": {"cached": True}})

        execution = ApiExecution(
            api_id=api.id,
            caller_user_id=key.user_id,
            api_key_id=key.id,
            params=params,
            status=ExecutionStatus.QUEUED,
        )
        db.add(execution)
        await db.commit()
        await db.refresh(execution)
        exec_id = execution.id
        api_id = api.id
        cache_ttl = api.cache_ttl_seconds

    status_url = f"/v1/executions/{exec_id}"
    done_channel = f"exec:done:{exec_id}"
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(done_channel)  # subscribe before XADD — no race with a fast worker
    try:
        await redis_client.xadd(
            "jobs:exec",
            {"payload": json.dumps({"execution_id": str(exec_id), "api_id": str(api_id), "params": params})},
        )

        if prefer == "respond-async":
            return JSONResponse({"execution_id": str(exec_id), "status_url": status_url}, status_code=202)

        try:
            await asyncio.wait_for(_next_pubsub_message(pubsub), timeout=settings.sync_wait_seconds)
        except asyncio.TimeoutError:
            return JSONResponse({"execution_id": str(exec_id), "status_url": status_url}, status_code=202)
    finally:
        await pubsub.unsubscribe(done_channel)
        await pubsub.aclose()

    raw_result = await redis_client.get(f"exec:result:{exec_id}")
    if raw_result is None:
        return JSONResponse({"execution_id": str(exec_id), "status_url": status_url}, status_code=202)

    result = json.loads(raw_result)
    if result["status"] != "succeeded":
        raise HTTPException(status_code=502, detail=result.get("error", "execution failed"))

    if cache_key and cache_ttl > 0:
        await redis_client.set(cache_key, json.dumps(result["data"]), ex=cache_ttl)

    return JSONResponse({
        "data": result["data"],
        "meta": {"cached": False, "duration_ms": result.get("duration_ms"), "execution_id": str(exec_id)},
    })


@public_app.get("/executions/{execution_id}")
async def get_execution(
    execution_id: uuid.UUID,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    key = await _authenticate_key(x_api_key)

    async with async_session() as db:
        execution = await db.get(ApiExecution, execution_id)
        if execution is None or execution.api_key_id != key.id:
            raise HTTPException(status_code=404, detail="execution not found")

        if execution.status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING):
            return JSONResponse({"execution_id": str(execution_id), "status": execution.status.value})

        raw_result = await redis_client.get(f"exec:result:{execution_id}")
        if raw_result is not None:
            result = json.loads(raw_result)
            if result["status"] == "succeeded":
                return JSONResponse({
                    "data": result["data"],
                    "meta": {"cached": False, "duration_ms": result.get("duration_ms"), "execution_id": str(execution_id)},
                })
            raise HTTPException(status_code=502, detail=result.get("error", "execution failed"))

        # Redis result already expired (EX 600) — fall back to the DB row.
        if execution.status == ExecutionStatus.SUCCEEDED:
            return JSONResponse({
                "data": execution.result,
                "meta": {"cached": False, "duration_ms": execution.duration_ms, "execution_id": str(execution_id)},
            })
        raise HTTPException(status_code=502, detail=execution.error_message or "execution failed")


async def _owner_session_user_id(request: Request) -> uuid.UUID | None:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    user_id_str = await redis_client.hget(f"sess:{sid}", "user_id")
    return uuid.UUID(user_id_str) if user_id_str else None


@public_app.get("/apis/{slug}/openapi.json")
async def get_openapi_spec(
    slug: str,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    async with async_session() as db:
        result = await db.execute(select(CustomApi).where(CustomApi.slug == slug))
        api = result.scalar_one_or_none()
    if api is None:
        raise HTTPException(status_code=404, detail="api not found")

    # Docs pages are browsed from a session (the owner, viewing their own
    # ApiDocs page) but /v1/run itself needs a real key — accept either here.
    authorized = False
    if x_api_key:
        key = await _authenticate_key(x_api_key)
        authorized = key.user_id == api.owner_id
    if not authorized:
        session_user_id = await _owner_session_user_id(request)
        authorized = session_user_id is not None and session_user_id == api.owner_id

    if not authorized:
        raise HTTPException(status_code=401, detail="unauthorized")
    if api.openapi_spec is None:
        raise HTTPException(status_code=404, detail="spec not generated yet")
    return JSONResponse(api.openapi_spec)
