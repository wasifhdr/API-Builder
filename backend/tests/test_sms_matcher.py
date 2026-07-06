from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.api import ApiAccessGrant, CustomApi
from app.models.billing import (
    PaymentPurpose,
    PaymentStatus,
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


async def test_matcher_exact_amount_verifies_and_activates_subscription(db, make_user):
    user = await make_user()
    intent = await payments.create_intent(user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
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
    intent = await payments.create_intent(user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
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
    intent = await payments.create_intent(user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
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

    intent = await payments.create_intent(user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
    intent = await payments.submit_trx(intent.id, user.id, "TRXEARLYSMS1", db)
    assert intent.status == PaymentStatus.VERIFIED


async def test_matcher_submit_before_sms_still_matches(db, make_user):
    user = await make_user()
    intent = await payments.create_intent(user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
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

    intent_a = await payments.create_intent(user_a.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
    await payments.submit_trx(intent_a.id, user_a.id, "TRXSHARED001", db)

    intent_b = await payments.create_intent(user_b.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO)
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


async def test_api_access_purchase_creates_grant(db, make_user):
    owner = await make_user()
    buyer = await make_user()
    api = await _make_custom_api(db, owner, price_bdt=Decimal("50.00"))

    intent = await payments.create_intent(buyer.id, PaymentPurpose.API_ACCESS, db, api_id=api.id)
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


async def test_create_intent_rejects_free_api(db, make_user):
    owner = await make_user()
    buyer = await make_user()
    api = await _make_custom_api(db, owner, price_bdt=None)

    with pytest.raises(PaymentError) as exc_info:
        await payments.create_intent(buyer.id, PaymentPurpose.API_ACCESS, db, api_id=api.id)
    assert exc_info.value.status_code == 400
