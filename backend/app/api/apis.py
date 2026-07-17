import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import current_user, get_effective_tier
from app.db import get_db
from app.models.api import ApiAccessGrant, ApiAllowedEmail, ApiInvite, ApiPricingMode, ApiVisibility, CustomApi, SpecStatus
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.user import User, UserRole
from app.models.workflow import Workflow, WorkflowStatus
from app.redis import redis_client
from app.schemas.api import (
    ApiExecutionOut,
    ApiStatsConsumerOut,
    ApiStatsDayOut,
    ApiStatsOut,
    CustomApiOut,
    CustomApiUpdate,
    ParameterOut,
)
from app.schemas.invite import AddAllowedEmailRequest, AllowedEmailOut, CreateInviteRequest, GrantOut, InviteOut
from app.services.grants import has_access
from app.services.plans import plan_for
from app.services.publish import sync_workflow_to_api

router = APIRouter(prefix="/apis", tags=["apis"])


async def _get_owned_api(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi:
    api = await db.get(CustomApi, api_id)
    if api is None or api.owner_id != user.id:
        raise HTTPException(status_code=404, detail="api not found")
    return api


async def _get_visible_api(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi:
    api = await db.get(CustomApi, api_id)
    if api is None or not await has_access(api, user.id, db):
        raise HTTPException(status_code=404, detail="api not found")
    return api


@router.get("", response_model=list[CustomApiOut])
async def list_apis(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CustomApi]:
    owned_result = await db.execute(select(CustomApi).where(CustomApi.owner_id == user.id))
    owned = list(owned_result.scalars().all())

    granted_result = await db.execute(
        select(CustomApi)
        .join(ApiAccessGrant, ApiAccessGrant.api_id == CustomApi.id)
        .where(ApiAccessGrant.user_id == user.id, ApiAccessGrant.revoked_at.is_(None))
    )
    granted = list(granted_result.scalars().all())

    seen = {a.id for a in owned}
    return owned + [a for a in granted if a.id not in seen]


@router.get("/{api_id}", response_model=CustomApiOut)
async def get_api(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    return await _get_visible_api(api_id, user, db)


@router.get("/{api_id}/parameters", response_model=list[ParameterOut])
async def get_api_parameters(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ParameterOut]:
    api = await _get_visible_api(api_id, user, db)
    raw = api.workflow_snapshot.get("parameters", [])
    result: list[ParameterOut] = []
    for p in raw:
        try:
            result.append(ParameterOut(**p))
        except (TypeError, ValidationError):
            continue  # skip malformed stored parameter entries rather than 500 the whole panel
    return result


@router.patch("/{api_id}", response_model=CustomApiOut)
async def update_api(
    api_id: uuid.UUID,
    body: CustomApiUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    api = await _get_owned_api(api_id, user, db)
    data = body.model_dump(exclude_unset=True)
    if data.get("cache_ttl_seconds") is not None:
        api.cache_ttl_seconds = data["cache_ttl_seconds"]
    if data.get("is_active") is not None:
        api.is_active = data["is_active"]

    new_pricing_mode = data.get("pricing_mode", api.pricing_mode)
    wants_shared = data.get("visibility") == ApiVisibility.SHARED
    wants_paid_mode = new_pricing_mode != ApiPricingMode.FREE
    if (wants_shared or wants_paid_mode) and user.role != UserRole.SUPER_ADMIN:
        tier = await get_effective_tier(user.id, db)
        can_share = (await plan_for(tier, db)).can_share
        if not can_share:
            raise HTTPException(status_code=403, detail="sharing and pricing require a Pro or Max plan")

    if "visibility" in data:
        api.visibility = data["visibility"]

    if "pricing_mode" in data or "price_bdt" in data:
        new_price = data.get("price_bdt", api.price_bdt)
        if new_pricing_mode == ApiPricingMode.FREE:
            new_price = None
        elif new_pricing_mode in (ApiPricingMode.PER_CALL, ApiPricingMode.SUBSCRIPTION) and (
            not new_price or new_price <= 0
        ):
            raise HTTPException(
                status_code=400, detail=f"{new_pricing_mode.value} pricing requires a price_bdt > 0"
            )
        api.pricing_mode = new_pricing_mode
        api.price_bdt = new_price

    if "included_call_quota" in data:
        api.included_call_quota = data["included_call_quota"]

    await db.commit()
    await db.refresh(api)
    return api


@router.post("/{api_id}/regenerate-spec", response_model=CustomApiOut)
async def regenerate_spec(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    api = await _get_owned_api(api_id, user, db)
    api.spec_status = SpecStatus.PENDING
    await db.commit()
    await db.refresh(api)
    await redis_client.xadd("jobs:llm", {"payload": json.dumps({"api_id": str(api.id)})})
    return api


@router.post("/{api_id}/sync", response_model=CustomApiOut)
async def sync_api(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CustomApi:
    api = await _get_owned_api(api_id, user, db)
    workflow = await db.get(Workflow, api.workflow_id)
    if workflow is None or workflow.status != WorkflowStatus.READY:
        raise HTTPException(
            status_code=400,
            detail="the recording must be ready (needs extraction) before syncing to the live API",
        )
    await sync_workflow_to_api(api, workflow, db)
    return api


@router.get("/{api_id}/executions", response_model=list[ApiExecutionOut])
async def list_executions(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiExecution]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiExecution)
        .where(ApiExecution.api_id == api.id)
        .order_by(ApiExecution.created_at.desc())
        .limit(50)
    )
    return list(result.scalars().all())


@router.post("/{api_id}/invites", response_model=InviteOut, status_code=201)
async def create_invite(
    api_id: uuid.UUID,
    body: CreateInviteRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiInvite:
    api = await _get_owned_api(api_id, user, db)
    if user.role != UserRole.SUPER_ADMIN:
        tier = await get_effective_tier(user.id, db)
        can_share = (await plan_for(tier, db)).can_share
        if not can_share:
            raise HTTPException(status_code=403, detail="invites require a Pro or Max plan")
    if api.visibility != ApiVisibility.SHARED:
        raise HTTPException(status_code=400, detail="set visibility to shared before creating invites")

    invite = ApiInvite(
        api_id=api.id,
        created_by=user.id,
        token=secrets.token_urlsafe(24),
        max_uses=body.max_uses,
        expires_at=body.expires_at,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.get("/{api_id}/invites", response_model=list[InviteOut])
async def list_invites(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiInvite]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiInvite).where(ApiInvite.api_id == api.id).order_by(ApiInvite.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{api_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    api_id: uuid.UUID,
    invite_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api = await _get_owned_api(api_id, user, db)
    invite = await db.get(ApiInvite, invite_id)
    if invite is None or invite.api_id != api.id:
        raise HTTPException(status_code=404, detail="invite not found")
    invite.revoked_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/{api_id}/allowed-emails", response_model=AllowedEmailOut, status_code=201)
async def add_allowed_email(
    api_id: uuid.UUID,
    body: AddAllowedEmailRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiAllowedEmail:
    api = await _get_owned_api(api_id, user, db)

    already_allowed = await db.execute(
        select(ApiAllowedEmail).where(ApiAllowedEmail.api_id == api.id, ApiAllowedEmail.email == body.email)
    )
    is_new_email = already_allowed.scalar_one_or_none() is None
    # A re-add of an already-allowed email isn't a new invitee (and will 409
    # below regardless) — only a genuinely new email should count against cap.
    if is_new_email and user.role != UserRole.SUPER_ADMIN:
        tier = await get_effective_tier(user.id, db)
        max_invitees = (await plan_for(tier, db)).max_invitees_per_api
        if max_invitees is not None:
            count_result = await db.execute(
                select(func.count()).select_from(ApiAllowedEmail).where(ApiAllowedEmail.api_id == api.id)
            )
            if count_result.scalar_one() >= max_invitees:
                raise HTTPException(
                    status_code=403,
                    detail=f"this plan allows at most {max_invitees} invitees per API",
                )

    entry = ApiAllowedEmail(api_id=api.id, email=body.email, added_by=user.id)
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="that email is already allowed for this API") from exc
    await db.refresh(entry)
    return entry


@router.get("/{api_id}/allowed-emails", response_model=list[AllowedEmailOut])
async def list_allowed_emails(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiAllowedEmail]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiAllowedEmail)
        .where(ApiAllowedEmail.api_id == api.id)
        .order_by(ApiAllowedEmail.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{api_id}/allowed-emails/{email_id}", status_code=204)
async def remove_allowed_email(
    api_id: uuid.UUID,
    email_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api = await _get_owned_api(api_id, user, db)
    entry = await db.get(ApiAllowedEmail, email_id)
    if entry is None or entry.api_id != api.id:
        raise HTTPException(status_code=404, detail="allowed email not found")
    await db.delete(entry)
    await db.commit()


@router.get("/{api_id}/grants", response_model=list[GrantOut])
async def list_grants(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiAccessGrant]:
    api = await _get_owned_api(api_id, user, db)
    result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id).order_by(ApiAccessGrant.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{api_id}/grants/{grant_id}", status_code=204)
async def revoke_grant(
    api_id: uuid.UUID,
    grant_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    api = await _get_owned_api(api_id, user, db)
    grant = await db.get(ApiAccessGrant, grant_id)
    if grant is None or grant.api_id != api.id:
        raise HTTPException(status_code=404, detail="grant not found")
    grant.revoked_at = datetime.now(timezone.utc)
    await db.commit()


async def _get_owned_api_or_super_admin(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi:
    api = await db.get(CustomApi, api_id)
    if api is None:
        raise HTTPException(status_code=404, detail="api not found")
    if api.owner_id != user.id and user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=404, detail="api not found")
    return api


def _dhaka_day(column):
    """Truncates a UTC timestamp column to its Asia/Dhaka calendar day."""
    return func.date_trunc("day", func.timezone(settings.quota_tz, column))


@router.get("/{api_id}/stats", response_model=ApiStatsOut)
async def get_api_stats(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiStatsOut:
    api = await _get_owned_api_or_super_admin(api_id, user, db)
    now = datetime.now(timezone.utc)
    window_7d_start = now - timedelta(days=7)
    window_30d_start = now - timedelta(days=30)
    tz = ZoneInfo(settings.quota_tz)
    today_dhaka = now.astimezone(tz).date()
    first_day_dhaka = today_dhaka - timedelta(days=13)

    lifetime_row = (
        await db.execute(
            select(
                func.count().label("total_calls"),
                func.max(ApiExecution.created_at).label("last_called_at"),
            ).where(ApiExecution.api_id == api.id)
        )
    ).one()

    window_row = (
        await db.execute(
            select(
                func.count().label("calls_7d"),
                func.count(case((ApiExecution.status == ExecutionStatus.SUCCEEDED, 1))).label("succeeded_7d"),
                func.avg(case((ApiExecution.duration_ms.is_not(None), ApiExecution.duration_ms))).label(
                    "avg_duration_ms_7d"
                ),
                func.count(case((ApiExecution.cache_hit.is_(True), 1))).label("cache_hits_7d"),
            ).where(ApiExecution.api_id == api.id, ApiExecution.created_at >= window_7d_start)
        )
    ).one()

    calls_7d = window_row.calls_7d or 0
    succeeded_7d = window_row.succeeded_7d or 0
    cache_hits_7d = window_row.cache_hits_7d or 0
    success_rate_7d = (succeeded_7d / calls_7d) if calls_7d else 0.0
    cache_hit_rate_7d = (cache_hits_7d / calls_7d) if calls_7d else 0.0
    avg_duration_ms_7d = float(window_row.avg_duration_ms_7d) if window_row.avg_duration_ms_7d is not None else None

    day_bucket = _dhaka_day(ApiExecution.created_at)
    day_rows = (
        await db.execute(
            select(
                day_bucket.label("day"),
                func.count().label("total"),
                func.count(case((ApiExecution.status == ExecutionStatus.SUCCEEDED, 1))).label("succeeded"),
            )
            .where(
                ApiExecution.api_id == api.id,
                ApiExecution.created_at >= datetime(
                    first_day_dhaka.year, first_day_dhaka.month, first_day_dhaka.day, tzinfo=tz
                ),
            )
            .group_by(day_bucket)
        )
    ).all()
    by_day = {row.day.date(): (row.total, row.succeeded) for row in day_rows}

    calls_by_day: list[ApiStatsDayOut] = []
    for offset in range(14):
        day = first_day_dhaka + timedelta(days=offset)
        total, succeeded = by_day.get(day, (0, 0))
        calls_by_day.append(ApiStatsDayOut(date=day.isoformat(), total=total, succeeded=succeeded))

    consumer_name = func.coalesce(User.username, User.email).label("name")
    consumer_rows = (
        await db.execute(
            select(consumer_name, func.count().label("calls_30d"))
            .select_from(ApiExecution)
            .join(User, User.id == ApiExecution.caller_user_id)
            .where(
                ApiExecution.api_id == api.id,
                ApiExecution.caller_user_id.is_not(None),
                ApiExecution.created_at >= window_30d_start,
            )
            .group_by(consumer_name)
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()
    top_consumers = [
        ApiStatsConsumerOut(name=row.name, calls_30d=row.calls_30d) for row in consumer_rows
    ]

    return ApiStatsOut(
        total_calls=lifetime_row.total_calls or 0,
        calls_7d=calls_7d,
        success_rate_7d=success_rate_7d,
        avg_duration_ms_7d=avg_duration_ms_7d,
        cache_hit_rate_7d=cache_hit_rate_7d,
        calls_by_day=calls_by_day,
        top_consumers=top_consumers,
        last_called_at=lifetime_row.last_called_at,
    )
