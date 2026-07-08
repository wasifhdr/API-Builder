from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.api import admin as admin_api
from app.api import billing as billing_api
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.user import UserRole
from app.models.wallet import BUCKET_EARNINGS, REASON_CALL_EARNING, CashoutStatus
from app.schemas.admin import CashoutPayRequest, CashoutRejectRequest
from app.schemas.billing import CashoutRequestIn, SweepRequest
from app.services import wallet
from app.services.wallet import InsufficientBalance


async def _give_tier(db, user, tier: PlanTier):
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=user.id, tier=tier, status=SubscriptionStatus.ACTIVE,
        starts_at=now, expires_at=now + timedelta(days=30),
    ))
    await db.commit()


async def _make_super(db, make_user):
    user = await make_user()
    user.role = UserRole.SUPER_ADMIN
    await db.commit()
    await db.refresh(user)
    return user


async def _give_earnings(db, user, amount: str):
    await wallet.credit(user.id, Decimal(amount), REASON_CALL_EARNING, db, bucket=BUCKET_EARNINGS)
    await db.commit()


# --- cashout eligibility gate ---


async def test_cashout_rejected_on_pro(db, make_user):
    user = await make_user()
    await _give_tier(db, user, PlanTier.PRO)
    await _give_earnings(db, user, "300.00")

    with pytest.raises(HTTPException) as exc_info:
        await billing_api.create_cashout(
            body=CashoutRequestIn(amount_bdt=Decimal("100.00"), payout_msisdn="01711111111"),
            user=user, db=db,
        )
    assert exc_info.value.status_code == 403


async def test_cashout_allowed_on_max_holds_earnings(db, make_user):
    user = await make_user()
    await _give_tier(db, user, PlanTier.MAX)
    await _give_earnings(db, user, "300.00")

    cashout = await billing_api.create_cashout(
        body=CashoutRequestIn(amount_bdt=Decimal("200.00"), payout_msisdn="01711111111"), user=user, db=db,
    )
    assert cashout.status == CashoutStatus.REQUESTED

    _, earnings = await wallet.balances(user.id, db)
    assert earnings == Decimal("100.00")  # 200 held out pending approval


async def test_cashout_insufficient_earnings_rejected(db, make_user):
    user = await make_user()
    await _give_tier(db, user, PlanTier.MAX)
    await _give_earnings(db, user, "50.00")

    with pytest.raises(HTTPException) as exc_info:
        await billing_api.create_cashout(
            body=CashoutRequestIn(amount_bdt=Decimal("200.00"), payout_msisdn="01711111111"), user=user, db=db,
        )
    assert exc_info.value.status_code == 402


async def test_super_admin_can_cash_out(db, make_user):
    admin = await _make_super(db, make_user)
    await _give_earnings(db, admin, "100.00")

    cashout = await billing_api.create_cashout(
        body=CashoutRequestIn(amount_bdt=Decimal("100.00"), payout_msisdn="01711111111"), user=admin, db=db,
    )
    assert cashout.status == CashoutStatus.REQUESTED


# --- admin pay / reject ---


async def test_admin_pay_cashout_records_trx_and_audits(db, make_user):
    user = await make_user()
    admin = await _make_super(db, make_user)
    await _give_tier(db, user, PlanTier.MAX)
    await _give_earnings(db, user, "200.00")
    cashout = await billing_api.create_cashout(
        body=CashoutRequestIn(amount_bdt=Decimal("200.00"), payout_msisdn="01711111111"), user=user, db=db,
    )

    result = await admin_api.pay_cashout(
        cashout_id=cashout.id, body=CashoutPayRequest(bkash_trx_id="TRXCASHOUT01"), admin=admin, db=db,
    )
    assert result.status == CashoutStatus.PAID
    assert result.bkash_trx_id == "TRXCASHOUT01"

    actions = await admin_api.list_audit_log(db=db)
    assert any(a.action == "cashout.pay" for a in actions)

    # earnings stay held (money genuinely left the platform) — not returned
    _, earnings = await wallet.balances(user.id, db)
    assert earnings == Decimal("0")


async def test_admin_reject_cashout_returns_funds_to_earnings(db, make_user):
    user = await make_user()
    admin = await _make_super(db, make_user)
    await _give_tier(db, user, PlanTier.MAX)
    await _give_earnings(db, user, "200.00")
    cashout = await billing_api.create_cashout(
        body=CashoutRequestIn(amount_bdt=Decimal("200.00"), payout_msisdn="01711111111"), user=user, db=db,
    )

    _, earnings_before = await wallet.balances(user.id, db)
    assert earnings_before == Decimal("0")

    result = await admin_api.reject_cashout(
        cashout_id=cashout.id, body=CashoutRejectRequest(note="bad number"), admin=admin, db=db,
    )
    assert result.status == CashoutStatus.REJECTED

    _, earnings_after = await wallet.balances(user.id, db)
    assert earnings_after == Decimal("200.00")


async def test_admin_cannot_pay_already_decided_cashout(db, make_user):
    user = await make_user()
    admin = await _make_super(db, make_user)
    await _give_tier(db, user, PlanTier.MAX)
    await _give_earnings(db, user, "100.00")
    cashout = await billing_api.create_cashout(
        body=CashoutRequestIn(amount_bdt=Decimal("100.00"), payout_msisdn="01711111111"), user=user, db=db,
    )
    await admin_api.pay_cashout(
        cashout_id=cashout.id, body=CashoutPayRequest(bkash_trx_id="TRXONE"), admin=admin, db=db,
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_api.pay_cashout(
            cashout_id=cashout.id, body=CashoutPayRequest(bkash_trx_id="TRXTWO"), admin=admin, db=db,
        )
    assert exc_info.value.status_code == 400


# --- sweep ---


async def test_sweep_moves_earnings_to_balance_and_is_spendable(db, make_user):
    user = await make_user()
    await _give_earnings(db, user, "150.00")

    result = await billing_api.sweep_wallet(body=SweepRequest(amount_bdt=None), user=user, db=db)
    assert result.swept_bdt == Decimal("150.00")
    assert result.balance_bdt == Decimal("150.00")
    assert result.earnings_bdt == Decimal("0")

    # spendable on the next call — an ordinary debit against balance succeeds
    new_balance = await wallet.debit(user.id, Decimal("50.00"), "call_debit", db)
    assert new_balance == Decimal("100.00")


async def test_sweep_partial_amount(db, make_user):
    user = await make_user()
    await _give_earnings(db, user, "150.00")

    result = await billing_api.sweep_wallet(body=SweepRequest(amount_bdt=Decimal("50.00")), user=user, db=db)
    assert result.swept_bdt == Decimal("50.00")
    assert result.balance_bdt == Decimal("50.00")
    assert result.earnings_bdt == Decimal("100.00")


async def test_sweep_more_than_earnings_raises(db, make_user):
    user = await make_user()
    await _give_earnings(db, user, "50.00")

    with pytest.raises(InsufficientBalance):
        await wallet.sweep(user.id, Decimal("100.00"), db)
