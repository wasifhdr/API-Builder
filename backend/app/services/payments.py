import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.billing import PaymentPurpose, PaymentStatus, PaymentTransaction
from app.services import sms_matcher

RECHARGE_MIN_BDT = Decimal("10")
RECHARGE_MAX_BDT = Decimal("100000")


class PaymentError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def create_intent(
    user_id: uuid.UUID,
    purpose: PaymentPurpose,
    db: AsyncSession,
    amount_bdt: Decimal | None = None,
) -> PaymentTransaction:
    """Creates a bKash payment intent. RECHARGE is the only purpose this can
    still create — subscriptions and one-time API access are funded from the
    wallet (see services/subscriptions.py and api/invites.py::accept_invite)
    since Phase W2. The SUBSCRIPTION/API_ACCESS enum values remain only to
    describe any pre-W2 transaction rows still being verified."""
    if purpose != PaymentPurpose.RECHARGE:
        raise PaymentError(400, "no longer a bKash purpose — fund your wallet and pay from it")

    if amount_bdt is None or amount_bdt < RECHARGE_MIN_BDT or amount_bdt > RECHARGE_MAX_BDT:
        raise PaymentError(
            400, f"amount_bdt must be between {RECHARGE_MIN_BDT} and {RECHARGE_MAX_BDT}"
        )
    transaction = PaymentTransaction(
        user_id=user_id,
        purpose=purpose,
        amount_expected_bdt=amount_bdt,
        status=PaymentStatus.PENDING,
    )

    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def submit_trx(transaction_id: uuid.UUID, user_id: uuid.UUID, trx_id: str, db: AsyncSession) -> PaymentTransaction:
    transaction = await db.get(PaymentTransaction, transaction_id)
    if transaction is None or transaction.user_id != user_id:
        raise PaymentError(404, "payment intent not found")
    if transaction.status != PaymentStatus.PENDING:
        raise PaymentError(400, f"intent is already {transaction.status.value}")

    age = datetime.now(timezone.utc) - transaction.created_at
    if age > timedelta(hours=settings.payment_intent_ttl_hours):
        transaction.status = PaymentStatus.EXPIRED
        await db.commit()
        raise PaymentError(400, "this payment intent has expired — create a new one")

    transaction.bkash_trx_id = trx_id.strip().upper()
    transaction.status = PaymentStatus.SUBMITTED
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise PaymentError(409, "this TrxID has already been submitted") from exc

    await db.refresh(transaction)
    await sms_matcher.try_match(transaction.id, db)
    await db.refresh(transaction)
    return transaction
