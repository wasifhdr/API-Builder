from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.api import ApiAccessGrant, CustomApi
from app.models.billing import (
    PaymentPurpose,
    PaymentStatus,
    PaymentTransaction,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from app.models.workflow import Workflow
from app.services import payments, sms_matcher
from app.services.payments import PaymentError

SMS_WORDINGS = [
    (
        "You have received Tk 100.00 from 01712345678. Fee Tk 0.00. Balance Tk 500.00. "
        "TrxID 9AB7CXXXX at 05/07/2026 14:22",
        Decimal("100.00"), "9AB7CXXXX", "01712345678",
    ),
    (
        "Payment of Tk 1,500 received from 01898765432. TrxID: 8KJ2MABCD1",
        Decimal("1500"), "8KJ2MABCD1", "01898765432",
    ),
    (
        "You have received Tk500.00 from 01555555555. Ref abc123 TrxID CBA987WXYZ. Balance Tk 2,000.50",
        Decimal("500.00"), "CBA987WXYZ", "01555555555",
    ),
    (
        "Cash In Tk 250.00 from agent. TrxID: ZZZ11122XY. Balance Tk 750.00",
        Decimal("250.00"), "ZZZ11122XY", None,
    ),
]


@pytest.mark.parametrize("text,amount,trx_id,msisdn", SMS_WORDINGS)
def test_parse_sms_various_wordings(text, amount, trx_id, msisdn):
    parsed = sms_matcher.parse_sms(text)
    assert parsed["amount"] == amount
    assert parsed["trx_id"] == trx_id
    assert parsed["msisdn"] == msisdn


async def _make_custom_api(db, user, price_bdt=None):
    workflow = Workflow(user_id=user.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=user.id,
        slug=f"test-api-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        price_bdt=price_bdt,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def _make_legacy_transaction(db, user, *, purpose, amount_bdt, plan_tier=None, api_id=None):
    """Since Phase W2, create_intent can no longer create SUBSCRIPTION/
    API_ACCESS transactions — but pre-W2 rows can still exist and need to
    verify/activate correctly via submit-trx + SMS match. Constructs one of
    those legacy rows directly, bypassing create_intent's new RECHARGE-only
    validation."""
    transaction = PaymentTransaction(
        user_id=user.id,
        purpose=purpose,
        plan_tier=plan_tier,
        api_id=api_id,
        amount_expected_bdt=amount_bdt,
        status=PaymentStatus.PENDING,
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def test_matcher_exact_amount_verifies_and_activates_subscription(db, make_user):
    user = await make_user()
    intent = await _make_legacy_transaction(
        db, user, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    intent = await payments.submit_trx(intent.id, user.id, "TRXEXACT001", db)
    assert intent.status == PaymentStatus.SUBMITTED

    receipt = await sms_matcher.ingest_sms(
        "You have received Tk 100.00 from 01712345678. TrxID TRXEXACT001",
        "bKash", datetime.now(timezone.utc), db,
    )
    assert receipt is not None

    await db.refresh(intent)
    assert intent.status == PaymentStatus.VERIFIED
    assert intent.verification_method.value == "auto_sms"

    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id, Subscription.status == SubscriptionStatus.ACTIVE)
    )
    sub = result.scalar_one()
    assert sub.tier == PlanTier.PRO


async def test_matcher_overpaid_still_verifies(db, make_user):
    user = await make_user()
    intent = await _make_legacy_transaction(
        db, user, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    intent = await payments.submit_trx(intent.id, user.id, "TRXOVERPAY01", db)

    await sms_matcher.ingest_sms(
        "You have received Tk 150.00 from 01712345678. TrxID TRXOVERPAY01",
        "bKash", datetime.now(timezone.utc), db,
    )
    await db.refresh(intent)
    assert intent.status == PaymentStatus.VERIFIED
    assert intent.amount_received_bdt == Decimal("150.00")


async def test_matcher_underpaid_leaves_submitted_with_note(db, make_user):
    user = await make_user()
    intent = await _make_legacy_transaction(
        db, user, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    intent = await payments.submit_trx(intent.id, user.id, "TRXUNDERPAY1", db)

    await sms_matcher.ingest_sms(
        "You have received Tk 50.00 from 01712345678. TrxID TRXUNDERPAY1",
        "bKash", datetime.now(timezone.utc), db,
    )
    await db.refresh(intent)
    assert intent.status == PaymentStatus.SUBMITTED
    assert intent.note is not None and "underpaid" in intent.note


async def test_matcher_sms_before_submit_still_matches(db, make_user):
    user = await make_user()
    await sms_matcher.ingest_sms(
        "You have received Tk 100.00 from 01712345678. TrxID TRXEARLYSMS1",
        "bKash", datetime.now(timezone.utc), db,
    )

    intent = await _make_legacy_transaction(
        db, user, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    intent = await payments.submit_trx(intent.id, user.id, "TRXEARLYSMS1", db)
    assert intent.status == PaymentStatus.VERIFIED


async def test_matcher_submit_before_sms_still_matches(db, make_user):
    user = await make_user()
    intent = await _make_legacy_transaction(
        db, user, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    intent = await payments.submit_trx(intent.id, user.id, "TRXLATESMS01", db)
    assert intent.status == PaymentStatus.SUBMITTED

    await sms_matcher.ingest_sms(
        "You have received Tk 100.00 from 01712345678. TrxID TRXLATESMS01",
        "bKash", datetime.now(timezone.utc), db,
    )
    await db.refresh(intent)
    assert intent.status == PaymentStatus.VERIFIED


async def test_duplicate_trx_id_rejected(db, make_user):
    user_a = await make_user()
    user_b = await make_user()

    intent_a = await _make_legacy_transaction(
        db, user_a, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    await payments.submit_trx(intent_a.id, user_a.id, "TRXSHARED001", db)

    intent_b = await _make_legacy_transaction(
        db, user_b, purpose=PaymentPurpose.SUBSCRIPTION, amount_bdt=Decimal("100.00"), plan_tier=PlanTier.PRO
    )
    with pytest.raises(PaymentError) as exc_info:
        await payments.submit_trx(intent_b.id, user_b.id, "TRXSHARED001", db)
    assert exc_info.value.status_code == 409


async def test_webhook_dedupe_identical_delivery_ignored(db):
    received_at = datetime.now(timezone.utc)
    text = "You have received Tk 100.00 from 01712345678. TrxID trx-dedupe-001"

    first = await sms_matcher.ingest_sms(text, "bKash", received_at, db)
    second = await sms_matcher.ingest_sms(text, "bKash", received_at, db)

    assert first is not None
    assert second is None


async def test_legacy_api_access_purchase_creates_grant(db, make_user):
    owner = await make_user()
    buyer = await make_user()
    api = await _make_custom_api(db, owner, price_bdt=Decimal("50.00"))

    intent = await _make_legacy_transaction(
        db, buyer, purpose=PaymentPurpose.API_ACCESS, amount_bdt=Decimal("50.00"), api_id=api.id
    )
    intent = await payments.submit_trx(intent.id, buyer.id, "TRXAPIACCESS", db)

    await sms_matcher.ingest_sms(
        "You have received Tk 50.00 from 01712345678. TrxID TRXAPIACCESS",
        "bKash", datetime.now(timezone.utc), db,
    )
    await db.refresh(intent)
    assert intent.status == PaymentStatus.VERIFIED

    result = await db.execute(
        select(ApiAccessGrant).where(ApiAccessGrant.api_id == api.id, ApiAccessGrant.user_id == buyer.id)
    )
    grant = result.scalar_one()
    assert grant.granted_via.value == "purchase"
    assert grant.revoked_at is None


async def test_create_intent_rejects_subscription_purpose(db, make_user):
    user = await make_user()
    with pytest.raises(PaymentError) as exc_info:
        await payments.create_intent(user.id, PaymentPurpose.SUBSCRIPTION, db)
    assert exc_info.value.status_code == 400


async def test_create_intent_rejects_api_access_purpose(db, make_user):
    user = await make_user()
    with pytest.raises(PaymentError) as exc_info:
        await payments.create_intent(user.id, PaymentPurpose.API_ACCESS, db)
    assert exc_info.value.status_code == 400
