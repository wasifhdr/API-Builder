from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import apis as apis_api
from app.api import invites as invites_api
from app.models.api import (
    ApiAccessGrant,
    ApiAllowedEmail,
    ApiInvite,
    ApiPricingMode,
    ApiVisibility,
    CustomApi,
    GrantSource,
)
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.wallet import REASON_RECHARGE
from app.models.workflow import Workflow
from app.schemas.api import CustomApiUpdate
from app.schemas.invite import AddAllowedEmailRequest, CreateInviteRequest
from app.services import wallet
from app.services.grants import has_access


async def _allow_email(db, api, email):
    db.add(ApiAllowedEmail(api_id=api.id, email=email.lower(), added_by=api.owner_id))
    await db.commit()


async def _make_api(
    db, owner, *,
    visibility=ApiVisibility.PRIVATE, price_bdt=None,
    pricing_mode=ApiPricingMode.FREE, included_call_quota=None,
):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=visibility,
        price_bdt=price_bdt,
        pricing_mode=pricing_mode,
        included_call_quota=included_call_quota,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def _give_pro(db, user):
    now = datetime.now(timezone.utc)
    db.add(
        Subscription(
            user_id=user.id, tier=PlanTier.PRO, status=SubscriptionStatus.ACTIVE,
            starts_at=now, expires_at=now + timedelta(days=30),
        )
    )
    await db.commit()


# --- has_access (call-time enforcement) ---


async def test_owner_always_has_access(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)
    assert await has_access(api, owner.id, db) is True


async def test_non_owner_without_grant_denied(db, make_user):
    owner = await make_user()
    other = await make_user()
    api = await _make_api(db, owner)
    assert await has_access(api, other.id, db) is False


async def test_grantee_with_active_owner_tier_allowed(db, make_user):
    owner = await make_user()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    await _give_pro(db, owner)
    db.add(ApiAccessGrant(api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE))
    await db.commit()
    assert await has_access(api, grantee.id, db) is True


async def test_grantee_denied_when_owner_tier_lapsed(db, make_user):
    owner = await make_user()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    db.add(ApiAccessGrant(api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE))
    await db.commit()
    # owner never subscribed -> free tier -> sharing gate fails at call time
    assert await has_access(api, grantee.id, db) is False


async def test_revoked_grant_denied(db, make_user):
    owner = await make_user()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    await _give_pro(db, owner)
    db.add(
        ApiAccessGrant(
            api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE,
            revoked_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    assert await has_access(api, grantee.id, db) is False


async def test_expired_grant_denied(db, make_user):
    owner = await make_user()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    await _give_pro(db, owner)
    db.add(
        ApiAccessGrant(
            api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    await db.commit()
    assert await has_access(api, grantee.id, db) is False


# --- invite accept flow ---


async def test_accept_free_invite_grants_immediately(db, make_user):
    owner = await make_user()
    visitor = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    await _allow_email(db, api, visitor.email)
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-free-1")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result = await invites_api.accept_invite(token=invite.token, user=visitor, db=db)
    assert result.status == "granted"

    grant_result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == visitor.id)
    )
    grant = grant_result.scalar_one()
    assert grant.granted_via == GrantSource.INVITE

    await db.refresh(invite)
    assert invite.use_count == 1


async def test_accept_priced_invite_debits_wallet_and_grants(db, make_user):
    owner = await make_user()
    visitor = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED, price_bdt=Decimal("75.00"))
    await _allow_email(db, api, visitor.email)
    await wallet.credit(visitor.id, Decimal("100.00"), REASON_RECHARGE, db)
    await db.commit()
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-priced-1")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result = await invites_api.accept_invite(token=invite.token, user=visitor, db=db)
    assert result.status == "granted"

    balance, _ = await wallet.balances(visitor.id, db)
    assert balance == Decimal("25.00")

    # the debited price doesn't just vanish — it splits into owner earnings
    # + platform cut (owner is FREE tier here -> 0% cut -> keeps it all)
    _, owner_earnings = await wallet.balances(owner.id, db)
    assert owner_earnings == Decimal("75.00")

    grant_result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == visitor.id)
    )
    grant = grant_result.scalar_one()
    assert grant.granted_via == GrantSource.PURCHASE
    assert grant.invite_id == invite.id

    await db.refresh(invite)
    assert invite.use_count == 1


async def test_accept_priced_invite_insufficient_balance_grants_nothing(db, make_user):
    owner = await make_user()
    visitor = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED, price_bdt=Decimal("75.00"))
    await _allow_email(db, api, visitor.email)
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-priced-poor-1")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result = await invites_api.accept_invite(token=invite.token, user=visitor, db=db)
    assert result.status == "insufficient_balance"
    assert result.price_bdt == "75.00"
    assert result.balance_bdt == "0"

    grant_result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == visitor.id)
    )
    assert grant_result.scalar_one_or_none() is None

    await db.refresh(invite)
    assert invite.use_count == 0


async def test_accept_revoked_invite_rejected(db, make_user):
    owner = await make_user()
    visitor = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    invite = ApiInvite(
        api_id=api.id, created_by=owner.id, token="tok-revoked-1", revoked_at=datetime.now(timezone.utc)
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    with pytest.raises(HTTPException) as exc_info:
        await invites_api.accept_invite(token=invite.token, user=visitor, db=db)
    assert exc_info.value.status_code == 400


async def test_accept_max_uses_reached_rejected(db, make_user):
    owner = await make_user()
    visitor1 = await make_user()
    visitor2 = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    await _allow_email(db, api, visitor1.email)
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-limited-1", max_uses=1)
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result1 = await invites_api.accept_invite(token=invite.token, user=visitor1, db=db)
    assert result1.status == "granted"

    with pytest.raises(HTTPException) as exc_info:
        await invites_api.accept_invite(token=invite.token, user=visitor2, db=db)
    assert exc_info.value.status_code == 400


async def test_accept_invite_rejected_when_email_not_allowed(db, make_user):
    owner = await make_user()
    visitor = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-unapproved-1")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    with pytest.raises(HTTPException) as exc_info:
        await invites_api.accept_invite(token=invite.token, user=visitor, db=db)
    assert exc_info.value.status_code == 403

    grant_result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == visitor.id)
    )
    assert grant_result.scalar_one_or_none() is None
    await db.refresh(invite)
    assert invite.use_count == 0


async def test_accept_invite_already_granted_bypasses_allowlist(db, make_user):
    owner = await make_user()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    db.add(ApiAccessGrant(api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE))
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-regrant-1")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result = await invites_api.accept_invite(token=invite.token, user=grantee, db=db)
    assert result.status == "granted"


# --- allowed-emails management ---


async def test_add_and_list_allowed_email(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)

    added = await apis_api.add_allowed_email(
        api_id=api.id, body=AddAllowedEmailRequest(email=" Friend@Example.com "), user=owner, db=db
    )
    assert added.email == "friend@example.com"

    listed = await apis_api.list_allowed_emails(api_id=api.id, user=owner, db=db)
    assert [a.email for a in listed] == ["friend@example.com"]


async def test_add_allowed_email_requires_ownership(db, make_user):
    owner = await make_user()
    other = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.add_allowed_email(
            api_id=api.id, body=AddAllowedEmailRequest(email="friend@example.com"), user=other, db=db
        )
    assert exc_info.value.status_code == 404


async def test_add_duplicate_allowed_email_rejected(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    await apis_api.add_allowed_email(
        api_id=api.id, body=AddAllowedEmailRequest(email="friend@example.com"), user=owner, db=db
    )

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.add_allowed_email(
            api_id=api.id, body=AddAllowedEmailRequest(email="friend@example.com"), user=owner, db=db
        )
    assert exc_info.value.status_code == 409


async def test_add_allowed_email_respects_invitee_cap(db, make_user):
    from app.models.plan_settings import PlanSettings
    from app.services.plans import invalidate_cache

    db.add(PlanSettings(
        tier="pro", price_bdt=100, daily_creation_limit=50, can_share=True,
        monthly_call_quota=5000, platform_cut_pct=Decimal("25"), can_cashout=False,
        max_invitees_per_api=1,
    ))
    await db.commit()
    invalidate_cache()

    owner = await make_user()
    await _give_pro(db, owner)
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)

    await apis_api.add_allowed_email(
        api_id=api.id, body=AddAllowedEmailRequest(email="first@example.com"), user=owner, db=db
    )

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.add_allowed_email(
            api_id=api.id, body=AddAllowedEmailRequest(email="second@example.com"), user=owner, db=db
        )
    assert exc_info.value.status_code == 403

    invalidate_cache()


async def test_add_allowed_email_unlimited_on_max_tier(db, make_user):
    from datetime import datetime, timedelta, timezone

    from app.models.billing import Subscription, SubscriptionStatus

    owner = await make_user()
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=owner.id, tier=PlanTier.MAX, status=SubscriptionStatus.ACTIVE,
        starts_at=now, expires_at=now + timedelta(days=30),
    ))
    await db.commit()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)

    for i in range(3):
        added = await apis_api.add_allowed_email(
            api_id=api.id, body=AddAllowedEmailRequest(email=f"friend{i}@example.com"), user=owner, db=db
        )
        assert added.email == f"friend{i}@example.com"


async def test_remove_allowed_email(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED)
    added = await apis_api.add_allowed_email(
        api_id=api.id, body=AddAllowedEmailRequest(email="friend@example.com"), user=owner, db=db
    )

    await apis_api.remove_allowed_email(api_id=api.id, email_id=added.id, user=owner, db=db)

    listed = await apis_api.list_allowed_emails(api_id=api.id, user=owner, db=db)
    assert listed == []


# --- tier gating on visibility/price/invite creation ---


async def test_update_api_visibility_requires_pro_tier(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.update_api(
            api_id=api.id, body=CustomApiUpdate(visibility=ApiVisibility.SHARED), user=owner, db=db
        )
    assert exc_info.value.status_code == 403

    await _give_pro(db, owner)
    updated = await apis_api.update_api(
        api_id=api.id, body=CustomApiUpdate(visibility=ApiVisibility.SHARED), user=owner, db=db
    )
    assert updated.visibility == ApiVisibility.SHARED


async def test_create_invite_requires_shared_visibility(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)  # private
    await _give_pro(db, owner)

    with pytest.raises(HTTPException) as exc_info:
        await apis_api.create_invite(api_id=api.id, body=CreateInviteRequest(), user=owner, db=db)
    assert exc_info.value.status_code == 400
