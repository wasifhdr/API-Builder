from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api import invites as invites_api
from app.api.public import _consume_subscription_included_quota
from app.models.api import ApiAccessGrant, ApiAllowedEmail, ApiInvite, ApiPricingMode, ApiVisibility, CustomApi
from app.models.wallet import REASON_RECHARGE
from app.models.workflow import Workflow
from app.services import wallet
from app.services.quota import QuotaExceeded, get_api_calls_this_month

SUBSCRIPTION_DAYS = 30


async def _make_api(db, owner, *, price_bdt, included_call_quota=None):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-sub-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=ApiVisibility.SHARED,
        pricing_mode=ApiPricingMode.SUBSCRIPTION,
        price_bdt=price_bdt,
        included_call_quota=included_call_quota,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def _allow_email(db, api, email):
    db.add(ApiAllowedEmail(api_id=api.id, email=email.lower(), added_by=api.owner_id))
    await db.commit()


async def _make_invite(db, api, owner):
    invite = ApiInvite(api_id=api.id, created_by=owner.id, token=f"tok-sub-{api.id.hex[:8]}")
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


async def _get_grant(db, api_id, user_id):
    result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api_id, ApiAccessGrant.user_id == user_id)
    )
    return result.scalar_one_or_none()


# --- accept on a subscription-mode API ---


async def test_subscribe_debits_wallet_grants_with_expiry_and_splits_earnings(db, make_user):
    owner = await make_user()
    invitee = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"), included_call_quota=50)
    await _allow_email(db, api, invitee.email)
    await wallet.credit(invitee.id, Decimal("200.00"), REASON_RECHARGE, db)
    await db.commit()
    invite = await _make_invite(db, api, owner)

    result = await invites_api.accept_invite(token=invite.token, user=invitee, db=db)
    assert result.status == "granted"

    balance, _ = await wallet.balances(invitee.id, db)
    assert balance == Decimal("100.00")

    _, owner_earnings = await wallet.balances(owner.id, db)
    assert owner_earnings == Decimal("100.00")  # owner is FREE tier here -> 0% cut

    grant = await _get_grant(db, api.id, invitee.id)
    assert grant is not None
    assert grant.expires_at is not None
    now = datetime.now(timezone.utc)
    assert now < grant.expires_at <= now + timedelta(days=SUBSCRIPTION_DAYS, minutes=1)

    await db.refresh(invite)
    assert invite.use_count == 1


async def test_subscribe_insufficient_balance_creates_no_grant(db, make_user):
    owner = await make_user()
    invitee = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"))
    await _allow_email(db, api, invitee.email)
    invite = await _make_invite(db, api, owner)

    result = await invites_api.accept_invite(token=invite.token, user=invitee, db=db)
    assert result.status == "insufficient_balance"
    assert result.price_bdt == "100.00"

    assert await _get_grant(db, api.id, invitee.id) is None


async def test_subscribe_renewal_extends_expiry_and_resplits_earnings(db, make_user):
    owner = await make_user()
    invitee = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"))
    await _allow_email(db, api, invitee.email)
    await wallet.credit(invitee.id, Decimal("300.00"), REASON_RECHARGE, db)
    await db.commit()
    invite = await _make_invite(db, api, owner)

    first = await invites_api.accept_invite(token=invite.token, user=invitee, db=db)
    assert first.status == "granted"
    grant = await _get_grant(db, api.id, invitee.id)
    first_expiry = grant.expires_at

    second = await invites_api.accept_invite(token=invite.token, user=invitee, db=db)
    assert second.status == "granted"

    await db.refresh(grant)
    assert grant.expires_at == first_expiry + timedelta(days=SUBSCRIPTION_DAYS)

    balance, _ = await wallet.balances(invitee.id, db)
    assert balance == Decimal("100.00")  # 300 - 100 - 100
    _, owner_earnings = await wallet.balances(owner.id, db)
    assert owner_earnings == Decimal("200.00")  # two periods paid, both fully kept (0% cut)

    await db.refresh(invite)
    assert invite.use_count == 2


# --- included-call quota gate (public.py::_consume_subscription_included_quota) ---


async def test_included_quota_blocks_after_limit(db, redis, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"), included_call_quota=2)

    await _consume_subscription_included_quota(api, caller.id, is_owner_or_super=False, redis=redis, db=db)
    await _consume_subscription_included_quota(api, caller.id, is_owner_or_super=False, redis=redis, db=db)

    with pytest.raises(QuotaExceeded):
        await _consume_subscription_included_quota(api, caller.id, is_owner_or_super=False, redis=redis, db=db)

    usage = await get_api_calls_this_month(caller.id, api.id, redis, db)
    assert usage == 2


async def test_included_quota_unlimited_when_none(db, redis, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"), included_call_quota=None)

    for _ in range(10):
        await _consume_subscription_included_quota(api, caller.id, is_owner_or_super=False, redis=redis, db=db)


async def test_included_quota_skipped_for_owner_and_super(db, redis, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"), included_call_quota=1)

    for _ in range(3):
        await _consume_subscription_included_quota(api, owner.id, is_owner_or_super=True, redis=redis, db=db)


async def test_included_quota_not_applied_to_per_call_mode(db, redis, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner, price_bdt=Decimal("100.00"), included_call_quota=1)
    api.pricing_mode = ApiPricingMode.PER_CALL
    await db.commit()

    for _ in range(5):
        await _consume_subscription_included_quota(api, caller.id, is_owner_or_super=False, redis=redis, db=db)
