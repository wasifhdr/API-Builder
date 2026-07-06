from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.api import ApiAccessGrant, ApiInvite, CustomApi, GrantSource
from app.models.billing import PaymentPurpose
from app.models.user import User
from app.schemas.invite import AcceptInviteResult, InvitePreviewOut
from app.services import payments

router = APIRouter(prefix="/invites", tags=["invites"])


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
    if grant is not None and grant.revoked_at is None:
        return AcceptInviteResult(status="granted")

    invite.use_count += 1

    if not api.price_bdt or api.price_bdt <= 0:
        if grant is not None:
            grant.revoked_at = None
            grant.granted_via = GrantSource.INVITE
            grant.invite_id = invite.id
        else:
            db.add(
                ApiAccessGrant(
                    api_id=api.id, user_id=user.id, granted_via=GrantSource.INVITE, invite_id=invite.id
                )
            )
        await db.commit()
        return AcceptInviteResult(status="granted")

    await db.commit()
    intent = await payments.create_intent(user.id, PaymentPurpose.API_ACCESS, db, api_id=api.id)
    return AcceptInviteResult(
        status="payment_required",
        payment_intent_id=intent.id,
        amount_expected_bdt=str(intent.amount_expected_bdt),
    )
