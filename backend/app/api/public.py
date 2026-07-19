import asyncio
import hashlib
import json
import time
import uuid
from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import SESSION_COOKIE, get_effective_tier
from app.db import async_session
from app.models.api import ApiKey, ApiPricingMode, CustomApi
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.user import User, UserRole
from app.models.wallet import REASON_CALL_DEBIT
from app.redis import redis_client
from app.services import wallet
from app.services.grants import has_access
from app.services.param_coercion import ParamCoercionError, coerce_params
from app.services.plans import plan_for
from app.services.quota import QuotaExceeded, consume_api_subscription_quota, consume_call_quota
from app.services.wallet import InsufficientBalance

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
    now = time.time()
    minute = int(now // 60)
    rl_key = f"rl:key:{key_id}:{minute}"
    count = await redis_client.incr(rl_key)
    await redis_client.expire(rl_key, RATE_LIMIT_WINDOW_SECONDS)
    if count > RATE_LIMIT_PER_MINUTE:
        reset_seconds = 60 - int(now % 60)
        raise HTTPException(
            status_code=429,
            detail={
                "message": "rate limit exceeded",
                "limit": RATE_LIMIT_PER_MINUTE,
                "reset_seconds": reset_seconds,
            },
        )


def _price_for_call(api: CustomApi, is_owner_or_super: bool) -> Decimal | None:
    """What to charge the caller for one call — None means free. Only
    per_call-priced APIs ever charge, and never the owner or a super admin."""
    if api.pricing_mode == ApiPricingMode.PER_CALL and not is_owner_or_super:
        return api.price_bdt
    return None


async def _consume_subscription_included_quota(
    api: CustomApi, user_id: uuid.UUID, is_owner_or_super: bool, redis, db,
) -> None:
    """Raises QuotaExceeded once a subscription-mode API's included_call_quota
    for this caller is used up for the Dhaka month. No-op for any other
    pricing mode, the owner, a super admin, or an unlimited (None) quota —
    the monthly subscription already paid for the call either way."""
    if api.pricing_mode != ApiPricingMode.SUBSCRIPTION or is_owner_or_super or api.included_call_quota is None:
        return
    await consume_api_subscription_quota(user_id, api.id, api.included_call_quota, redis, db)


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

        key_owner = await db.get(User, key.user_id)
        api_owner = await db.get(User, api.owner_id)
        if (key_owner is not None and key_owner.suspended_at is not None) or (
            api_owner is not None and api_owner.suspended_at is not None
        ):
            raise HTTPException(status_code=403, detail="account suspended")

        if not await has_access(api, key.user_id, db):
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

        is_super = key_owner is not None and key_owner.role == UserRole.SUPER_ADMIN
        is_owner_or_super = key.user_id == api.owner_id or is_super
        price = _price_for_call(api, is_owner_or_super)

        try:
            await _consume_subscription_included_quota(api, key.user_id, is_owner_or_super, redis_client, db)
        except QuotaExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail={"detail": "monthly included calls used", "reset_seconds": exc.reset_seconds},
            ) from exc

        execution = ApiExecution(
            api_id=api.id,
            caller_user_id=key.user_id,
            api_key_id=key.id,
            params=params,
            status=ExecutionStatus.QUEUED,
        )
        db.add(execution)
        await db.flush()
        exec_id = execution.id

        if price:
            try:
                await wallet.debit(
                    key.user_id, price, REASON_CALL_DEBIT, db, api_id=api.id, execution_id=exec_id
                )
            except InsufficientBalance as exc:
                await db.rollback()
                raise HTTPException(
                    status_code=402,
                    detail={
                        "detail": "insufficient wallet balance",
                        "price_bdt": str(price),
                        "balance_bdt": str(exc.available),
                    },
                ) from exc

        if not is_super:
            # Usage is usage — a call counts toward the caller's monthly
            # allowance whether it's free, one-time-granted, or per-call paid,
            # and whether the caller is the API's own owner.
            caller_tier = await get_effective_tier(key.user_id, db)
            call_limit = (await plan_for(caller_tier, db)).monthly_call_quota
            try:
                await consume_call_quota(key.user_id, call_limit, redis_client, db)
            except QuotaExceeded as exc:
                await db.rollback()
                raise HTTPException(
                    status_code=429,
                    detail={"detail": "monthly call quota exceeded", "reset_seconds": exc.reset_seconds},
                ) from exc

        await db.commit()
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


async def _session_user_id(request: Request) -> uuid.UUID | None:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    user_id_str = await redis_client.hget(f"sess:{sid}", "user_id")
    return uuid.UUID(user_id_str) if user_id_str else None


async def _authorized_api_by_slug(
    slug: str, request: Request, x_api_key: str | None, db: AsyncSession
) -> CustomApi:
    """Resolves a slug to an API the current viewer may see. Docs pages are
    browsed from a session (owner or a grantee, viewing ApiDocs); an API key is
    also accepted so programmatic clients can fetch the spec. 404/401/403 behave
    exactly like /run."""
    result = await db.execute(select(CustomApi).where(CustomApi.slug == slug))
    api = result.scalar_one_or_none()
    if api is None:
        raise HTTPException(status_code=404, detail="api not found")

    if x_api_key:
        key = await _authenticate_key(x_api_key)
        candidate_user_id = key.user_id
    else:
        candidate_user_id = await _session_user_id(request)
        if candidate_user_id is None:
            raise HTTPException(status_code=401, detail="unauthorized")

    if not await has_access(api, candidate_user_id, db):
        raise HTTPException(status_code=403, detail="no access to this api")
    return api


@public_app.get("/apis/{slug}/openapi.json")
async def get_openapi_spec(
    slug: str,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    async with async_session() as db:
        api = await _authorized_api_by_slug(slug, request, x_api_key, db)
        if api.openapi_spec is None:
            raise HTTPException(status_code=404, detail="spec not generated yet")
        return JSONResponse(api.openapi_spec)


@public_app.get("/apis/{slug}/doc")
async def get_api_doc(
    slug: str,
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Status-aware doc payload for the ApiDocs page. Lets the UI show a
    'Generating Docs' state while spec_status is pending/generating and render
    the enriched spec once it is ready. `spec` is null until the first
    generation completes; during a re-generation it holds the previous spec."""
    async with async_session() as db:
        api = await _authorized_api_by_slug(slug, request, x_api_key, db)
        return JSONResponse({
            "name": api.name,
            "slug": api.slug,
            "status": api.spec_status.value,
            "spec": api.openapi_spec,
        })
