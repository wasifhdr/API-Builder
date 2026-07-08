import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.billing import PaymentPurpose, PaymentStatus
from app.models.wallet import REASON_CALL_DEBIT, REASON_RECHARGE, WalletLedger
from app.services import payments, sms_matcher, wallet
from app.services.payments import PaymentError

# --- create_intent(RECHARGE): amount validation ---


async def test_create_intent_recharge_requires_amount(db, make_user):
    user = await make_user()
    with pytest.raises(PaymentError) as exc_info:
        await payments.create_intent(user.id, PaymentPurpose.RECHARGE, db)
    assert exc_info.value.status_code == 400


async def test_create_intent_recharge_rejects_below_minimum(db, make_user):
    user = await make_user()
    with pytest.raises(PaymentError):
        await payments.create_intent(user.id, PaymentPurpose.RECHARGE, db, amount_bdt=Decimal("5"))


async def test_create_intent_recharge_rejects_above_maximum(db, make_user):
    user = await make_user()
    with pytest.raises(PaymentError):
        await payments.create_intent(user.id, PaymentPurpose.RECHARGE, db, amount_bdt=Decimal("200000"))


async def test_create_intent_recharge_accepts_valid_amount(db, make_user):
    user = await make_user()
    intent = await payments.create_intent(user.id, PaymentPurpose.RECHARGE, db, amount_bdt=Decimal("500"))
    assert intent.purpose == PaymentPurpose.RECHARGE
    assert intent.amount_expected_bdt == Decimal("500")
    assert intent.plan_tier is None
    assert intent.api_id is None


# --- recharge end-to-end: intent -> submit-trx -> SMS match -> wallet credit ---


async def test_recharge_credits_wallet_on_sms_match(db, make_user):
    user = await make_user()
    intent = await payments.create_intent(user.id, PaymentPurpose.RECHARGE, db, amount_bdt=Decimal("500.00"))
    intent = await payments.submit_trx(intent.id, user.id, "TRXRECHARGE1", db)
    assert intent.status == PaymentStatus.SUBMITTED

    receipt = await sms_matcher.ingest_sms(
        "You have received Tk 500.00 from 01712345678. TrxID TRXRECHARGE1",
        "bKash", datetime.now(timezone.utc), db,
    )
    assert receipt is not None

    await db.refresh(intent)
    assert intent.status == PaymentStatus.VERIFIED
    assert intent.verification_method.value == "auto_sms"

    balance, earnings = await wallet.balances(user.id, db)
    assert balance == Decimal("500.00")
    assert earnings == Decimal("0")

    result = await db.execute(select(WalletLedger).where(WalletLedger.user_id == user.id))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].reason == REASON_RECHARGE
    assert rows[0].bucket == "balance"
    assert rows[0].amount_bdt == Decimal("500.00")
    assert rows[0].balance_after_bdt == Decimal("500.00")
    assert rows[0].transaction_id == intent.id


async def test_recharge_underpaid_leaves_submitted_and_wallet_uncredited(db, make_user):
    user = await make_user()
    intent = await payments.create_intent(user.id, PaymentPurpose.RECHARGE, db, amount_bdt=Decimal("500.00"))
    intent = await payments.submit_trx(intent.id, user.id, "TRXRECHUNDER1", db)

    await sms_matcher.ingest_sms(
        "You have received Tk 100.00 from 01712345678. TrxID TRXRECHUNDER1",
        "bKash", datetime.now(timezone.utc), db,
    )
    await db.refresh(intent)
    assert intent.status == PaymentStatus.SUBMITTED

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("0")


# --- services/wallet.py: credit/debit atomicity ---


async def test_credit_creates_wallet_and_ledger_row(db, make_user):
    user = await make_user()
    new_balance = await wallet.credit(user.id, Decimal("100.00"), REASON_RECHARGE, db)
    await db.commit()
    assert new_balance == Decimal("100.00")

    balance, earnings = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")
    assert earnings == Decimal("0")


async def test_debit_success_updates_balance_and_writes_negative_ledger_row(db, make_user):
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), REASON_RECHARGE, db)
    await db.commit()

    new_balance = await wallet.debit(user.id, Decimal("40.00"), REASON_CALL_DEBIT, db)
    await db.commit()
    assert new_balance == Decimal("60.00")

    result = await db.execute(
        select(WalletLedger).where(WalletLedger.user_id == user.id).order_by(WalletLedger.created_at)
    )
    rows = result.scalars().all()
    assert len(rows) == 2
    assert rows[1].amount_bdt == Decimal("-40.00")
    assert rows[1].balance_after_bdt == Decimal("60.00")
    assert rows[1].reason == REASON_CALL_DEBIT


async def test_debit_insufficient_balance_leaves_balance_untouched(db, make_user):
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), REASON_RECHARGE, db)
    await db.commit()

    with pytest.raises(wallet.InsufficientBalance) as exc_info:
        await wallet.debit(user.id, Decimal("150.00"), REASON_CALL_DEBIT, db)
    assert exc_info.value.needed == Decimal("150.00")
    assert exc_info.value.available == Decimal("100.00")

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")

    result = await db.execute(select(WalletLedger).where(WalletLedger.user_id == user.id))
    rows = result.scalars().all()
    assert len(rows) == 1  # only the credit — the failed debit wrote nothing


async def test_debit_with_no_wallet_row_raises_insufficient_balance(db, make_user):
    user = await make_user()
    with pytest.raises(wallet.InsufficientBalance) as exc_info:
        await wallet.debit(user.id, Decimal("10.00"), REASON_CALL_DEBIT, db)
    assert exc_info.value.available == Decimal("0")


async def test_concurrent_debits_never_overdraw(engine, db, make_user):
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), REASON_RECHARGE, db)
    await db.commit()

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _attempt() -> str:
        async with session_maker() as session:
            try:
                await wallet.debit(user.id, Decimal("60.00"), REASON_CALL_DEBIT, session)
            except wallet.InsufficientBalance:
                await session.rollback()
                return "failed"
            await session.commit()
            return "succeeded"

    outcomes = await asyncio.gather(_attempt(), _attempt())
    assert sorted(outcomes) == ["failed", "succeeded"]

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("40.00")
