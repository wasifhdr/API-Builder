from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import apis as apis_api
from app.api import invites as invites_api
from app.models.api import ApiAccessGrant, ApiInvite, ApiVisibility, CustomApi, GrantSource
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.workflow import Workflow
from app.schemas.api import CustomApiUpdate
from app.schemas.invite import CreateInviteRequest
from app.services.grants import has_access


async def _make_api(db, owner, *, visibility=ApiVisibility.PRIVATE, price_bdt=None):
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


async def test_accept_priced_invite_creates_payment_intent(db, make_user):
    owner = await make_user()
    visitor = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED, price_bdt=Decimal("75.00"))
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-priced-1")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result = await invites_api.accept_invite(token=invite.token, user=visitor, db=db)
    assert result.status == "payment_required"
    assert result.amount_expected_bdt == "75.00"

    grant_result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == visitor.id)
    )
    assert grant_result.scalar_one_or_none() is None


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
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token="tok-limited-1", max_uses=1)
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    result1 = await invites_api.accept_invite(token=invite.token, user=visitor1, db=db)
    assert result1.status == "granted"

    with pytest.raises(HTTPException) as exc_info:
        await invites_api.accept_invite(token=invite.token, user=visitor2, db=db)
    assert exc_info.value.status_code == 400


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
