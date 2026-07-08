from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import billing as billing_api
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.user import UserRole
from app.models.wallet import REASON_RECHARGE, WalletLedger
from app.schemas.billing import SubscribeRequest
from app.services import wallet


async def _make_super(db, make_user):
    user = await make_user()
    user.role = UserRole.SUPER_ADMIN
    await db.commit()
    await db.refresh(user)
    return user


async def _fund(db, user, amount: str):
    await wallet.credit(user.id, Decimal(amount), REASON_RECHARGE, db)
    await db.commit()


# --- /billing/subscribe ---


async def test_subscribe_debits_wallet_and_activates_tier(db, make_user):
    user = await make_user()
    await _fund(db, user, "500")

    result = await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.PRO), user=user, db=db)
    assert result.tier == PlanTier.PRO

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("400.00")  # default Pro seed price is ৳100

    sub_result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.status == SubscriptionStatus.ACTIVE)
    )
    sub = sub_result.scalar_one()
    assert sub.tier == PlanTier.PRO

    ledger_result = await db.execute(select(WalletLedger).where(WalletLedger.user_id == user.id))
    rows = ledger_result.scalars().all()
    assert len(rows) == 2  # the recharge credit + the subscription debit
    debit_row = next(r for r in rows if r.reason == "subscription")
    assert debit_row.amount_bdt == Decimal("-100.00")


async def test_subscribe_insufficient_balance_no_tier_change(db, make_user):
    user = await make_user()

    with pytest.raises(HTTPException) as exc_info:
        await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.PRO), user=user, db=db)
    assert exc_info.value.status_code == 402
    assert exc_info.value.detail["shortfall_bdt"] == "100"

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("0")

    sub_result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.status == SubscriptionStatus.ACTIVE)
    )
    assert sub_result.scalar_one_or_none() is None


async def test_subscribe_rejects_super_admin(db, make_user):
    super_user = await _make_super(db, make_user)
    with pytest.raises(HTTPException) as exc_info:
        await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.PRO), user=super_user, db=db)
    assert exc_info.value.status_code == 400
    assert "super admin" in exc_info.value.detail


async def test_subscribe_rejects_free_tier(db, make_user):
    user = await make_user()
    await _fund(db, user, "500")
    with pytest.raises(HTTPException) as exc_info:
        await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.FREE), user=user, db=db)
    assert exc_info.value.status_code == 400


async def test_subscribe_same_tier_extends_expiry(db, make_user):
    user = await make_user()
    await _fund(db, user, "500")

    first = await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.PRO), user=user, db=db)
    second = await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.PRO), user=user, db=db)

    assert second.expires_at > first.expires_at
    assert (second.expires_at - first.expires_at).days == 30

    sub_result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.status == SubscriptionStatus.ACTIVE)
    )
    assert len(sub_result.scalars().all()) == 1


async def test_subscribe_upgrade_cancels_old_starts_new(db, make_user):
    user = await make_user()
    await _fund(db, user, "1000")

    await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.PRO), user=user, db=db)
    await billing_api.subscribe(body=SubscribeRequest(plan_tier=PlanTier.MAX), user=user, db=db)

    all_subs = (await db.execute(select(Subscription).where(Subscription.user_id == user.id))).scalars().all()
    assert len(all_subs) == 2
    statuses = {s.tier: s.status for s in all_subs}
    assert statuses[PlanTier.PRO] == SubscriptionStatus.CANCELLED
    assert statuses[PlanTier.MAX] == SubscriptionStatus.ACTIVE
