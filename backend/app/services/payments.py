import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.api import CustomApi
from app.models.billing import PaymentPurpose, PaymentStatus, PaymentTransaction, PlanTier
from app.services import sms_matcher
from app.services.plans import plan_for


class PaymentError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def create_intent(
    user_id: uuid.UUID,
    purpose: PaymentPurpose,
    db: AsyncSession,
    plan_tier: PlanTier | None = None,
    api_id: uuid.UUID | None = None,
    is_super: bool = False,
) -> PaymentTransaction:
    if purpose == PaymentPurpose.SUBSCRIPTION:
        if is_super:
            raise PaymentError(400, "super admins don't need plans")
        if plan_tier is None or plan_tier == PlanTier.FREE:
            raise PaymentError(400, "plan_tier must be pro or max")
        amount = Decimal((await plan_for(plan_tier, db)).price_bdt)
        transaction = PaymentTransaction(
            user_id=user_id,
            purpose=purpose,
            plan_tier=plan_tier,
            amount_expected_bdt=amount,
            status=PaymentStatus.PENDING,
        )
    else:
        if api_id is None:
            raise PaymentError(400, "api_id is required for api_access purpose")
        api = await db.get(CustomApi, api_id)
        if api is None:
            raise PaymentError(404, "api not found")
        if not api.price_bdt or api.price_bdt <= 0:
            raise PaymentError(400, "this api is free — accept the invite directly")
        transaction = PaymentTransaction(
            user_id=user_id,
            purpose=purpose,
            api_id=api_id,
            amount_expected_bdt=api.price_bdt,
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
