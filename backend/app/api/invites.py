from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.api import ApiAccessGrant, ApiAllowedEmail, ApiInvite, ApiPricingMode, CustomApi, GrantSource
from app.models.user import User
from app.models.wallet import REASON_API_ACCESS
from app.schemas.invite import AcceptInviteResult, InvitePreviewOut
from app.services import wallet
from app.services.wallet import InsufficientBalance

router = APIRouter(prefix="/invites", tags=["invites"])

SUBSCRIPTION_DAYS = 30


async def _get_invite_and_api(token: str, db: AsyncSession) -> tuple[ApiInvite, CustomApi]:
    result = await db.execute(select(ApiInvite).where(ApiInvite.token == token))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="invite not found")
    api = await db.get(CustomApi, invite.api_id)
    if api is None:
        raise HTTPException(status_code=404, detail="api not found")
    return invite, api


def _invite_invalid_reason(invite: ApiInvite) -> str | None:
    now = datetime.now(timezone.utc)
    if invite.revoked_at is not None:
        return "this invite has been revoked"
    if invite.expires_at is not None and invite.expires_at <= now:
        return "this invite has expired"
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        return "this invite has reached its usage limit"
    return None


@router.get("/{token}", response_model=InvitePreviewOut)
async def preview_invite(token: str, db: AsyncSession = Depends(get_db)) -> InvitePreviewOut:
    invite, api = await _get_invite_and_api(token, db)
    reason = _invite_invalid_reason(invite)
    return InvitePreviewOut(
        api_name=api.name,
        api_slug=api.slug,
        price_bdt=str(api.price_bdt) if api.price_bdt else None,
        pricing_mode=api.pricing_mode,
        valid=reason is None,
        reason=reason,
    )


@router.post("/{token}/accept", response_model=AcceptInviteResult)
async def accept_invite(
    token: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> AcceptInviteResult:
    invite, api = await _get_invite_and_api(token, db)
    reason = _invite_invalid_reason(invite)
    if reason is not None:
        raise HTTPException(status_code=400, detail=reason)

    existing = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == user.id)
    )
    grant = existing.scalar_one_or_none()
    is_subscription = api.pricing_mode == ApiPricingMode.SUBSCRIPTION
    # Subscription-mode acceptance is also how a renewal happens — an already
    # live grant must still go through payment+extension, never short-circuit.
    if grant is not None and grant.revoked_at is None and not is_subscription:
        return AcceptInviteResult(status="granted")

    allowed = await db.execute(
        select(ApiAllowedEmail).where(ApiAllowedEmail.api_id == api.id, ApiAllowedEmail.email == user.email)
    )
    if allowed.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=403,
            detail="your account's email hasn't been approved for this API — ask the owner to add it",
        )

    if is_subscription:
        try:
            await wallet.debit(user.id, api.price_bdt, REASON_API_ACCESS, db, api_id=api.id)
        except InsufficientBalance:
            balance, _ = await wallet.balances(user.id, db)
            return AcceptInviteResult(
                status="insufficient_balance", price_bdt=str(api.price_bdt), balance_bdt=str(balance),
            )
        await wallet.split_sale_proceeds(
            api.owner_id, api.price_bdt, db, api_id=api.id, counterparty_user_id=user.id,
        )

        now = datetime.now(timezone.utc)
        # A still-live grant renews from its current expiry (no lost paid-for
        # time); a lapsed or brand-new one starts a fresh 30-day period.
        base = grant.expires_at if (grant is not None and grant.expires_at and grant.expires_at > now) else now
        invite.use_count += 1
        if grant is not None:
            grant.revoked_at = None
            grant.granted_via = GrantSource.PURCHASE
            grant.invite_id = invite.id
            grant.expires_at = base + timedelta(days=SUBSCRIPTION_DAYS)
        else:
            db.add(ApiAccessGrant(
                api_id=api.id, user_id=user.id, granted_via=GrantSource.PURCHASE, invite_id=invite.id,
                expires_at=base + timedelta(days=SUBSCRIPTION_DAYS),
            ))
        await db.commit()
        return AcceptInviteResult(status="granted")

    priced = bool(api.price_bdt) and api.price_bdt > 0
    if priced:
        try:
            await wallet.debit(user.id, api.price_bdt, REASON_API_ACCESS, db, api_id=api.id)
        except InsufficientBalance:
            balance, _ = await wallet.balances(user.id, db)
            return AcceptInviteResult(
                status="insufficient_balance",
                price_bdt=str(api.price_bdt),
                balance_bdt=str(balance),
            )
        await wallet.split_sale_proceeds(
            api.owner_id, api.price_bdt, db, api_id=api.id, counterparty_user_id=user.id,
        )

    invite.use_count += 1
    granted_via = GrantSource.PURCHASE if priced else GrantSource.INVITE
    if grant is not None:
        grant.revoked_at = None
        grant.granted_via = granted_via
        grant.invite_id = invite.id
    else:
        db.add(ApiAccessGrant(api_id=api.id, user_id=user.id, granted_via=granted_via, invite_id=invite.id))
    await db.commit()
    return AcceptInviteResult(status="granted")
