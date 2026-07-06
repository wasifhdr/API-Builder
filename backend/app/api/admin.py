import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_effective_tier, require_admin
from app.db import get_db
from app.models.billing import (
    BkashSmsReceipt,
    PaymentStatus,
    PaymentTransaction,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    VerificationMethod,
)
from app.models.user import User
from app.schemas.admin import (
    AdminSmsOut,
    AdminTransactionOut,
    AdminUserOut,
    RejectRequest,
    TierOverrideRequest,
)
from app.services.sms_matcher import apply_verified_effects

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

ADMIN_OVERRIDE_DAYS = 30


@router.get("/transactions", response_model=list[AdminTransactionOut])
async def list_transactions(db: AsyncSession = Depends(get_db)) -> list[PaymentTransaction]:
    result = await db.execute(
        select(PaymentTransaction).order_by(PaymentTransaction.created_at.desc()).limit(200)
    )
    return list(result.scalars().all())


@router.post("/transactions/{transaction_id}/verify", response_model=AdminTransactionOut)
async def verify_transaction(
    transaction_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PaymentTransaction:
    result = await db.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == transaction_id).with_for_update()
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    if transaction.status not in (PaymentStatus.PENDING, PaymentStatus.SUBMITTED):
        raise HTTPException(status_code=400, detail=f"cannot verify a {transaction.status.value} transaction")

    transaction.status = PaymentStatus.VERIFIED
    transaction.verification_method = VerificationMethod.MANUAL_ADMIN
    transaction.verified_at = datetime.now(timezone.utc)
    transaction.verified_by_user_id = admin.id
    if transaction.amount_received_bdt is None:
        transaction.amount_received_bdt = transaction.amount_expected_bdt

    await apply_verified_effects(transaction, db)
    await db.commit()
    await db.refresh(transaction)
    return transaction


@router.post("/transactions/{transaction_id}/reject", response_model=AdminTransactionOut)
async def reject_transaction(
    transaction_id: uuid.UUID,
    body: RejectRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PaymentTransaction:
    transaction = await db.get(PaymentTransaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    transaction.status = PaymentStatus.REJECTED
    transaction.note = body.note
    transaction.verified_by_user_id = admin.id
    await db.commit()
    await db.refresh(transaction)
    return transaction


@router.get("/sms", response_model=list[AdminSmsOut])
async def list_sms(db: AsyncSession = Depends(get_db)) -> list[BkashSmsReceipt]:
    result = await db.execute(
        select(BkashSmsReceipt).order_by(BkashSmsReceipt.received_at.desc()).limit(200)
    )
    return list(result.scalars().all())


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[AdminUserOut]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    out = []
    for u in result.scalars().all():
        tier = await get_effective_tier(u.id, db)
        out.append(AdminUserOut(id=u.id, email=u.email, name=u.name, role=u.role, effective_tier=tier))
    return out


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def override_tier(
    user_id: uuid.UUID,
    body: TierOverrideRequest,
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.status = SubscriptionStatus.CANCELLED

    if body.tier != PlanTier.FREE:
        now = datetime.now(timezone.utc)
        db.add(
            Subscription(
                user_id=user_id,
                tier=body.tier,
                status=SubscriptionStatus.ACTIVE,
                starts_at=now,
                expires_at=now + timedelta(days=ADMIN_OVERRIDE_DAYS),
            )
        )

    await db.commit()
    tier = await get_effective_tier(user_id, db)
    return AdminUserOut(id=user.id, email=user.email, name=user.name, role=user.role, effective_tier=tier)
