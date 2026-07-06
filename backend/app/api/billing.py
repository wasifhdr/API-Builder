import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import current_user
from app.db import get_db
from app.models.billing import PaymentTransaction
from app.models.user import User, UserRole
from app.schemas.billing import (
    BillingConfigOut,
    CreateIntentRequest,
    PaymentIntentOut,
    PlanOut,
    SubmitTrxRequest,
)
from app.services import payments
from app.services.payments import PaymentError
from app.services.plans import get_plans

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(db: AsyncSession = Depends(get_db)) -> list[PlanOut]:
    plans = await get_plans(db)
    return [
        PlanOut(
            tier=p.tier,
            name=p.name,
            price_bdt=p.price_bdt,
            daily_creation_limit=p.daily_creation_limit,
            can_share=p.can_share,
        )
        for p in plans.values()
    ]


@router.get("/config", response_model=BillingConfigOut)
async def billing_config() -> BillingConfigOut:
    return BillingConfigOut(receive_msisdn=settings.bkash_receive_msisdn)


@router.post("/intents", response_model=PaymentIntentOut, status_code=201)
async def create_intent(
    body: CreateIntentRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentTransaction:
    try:
        return await payments.create_intent(
            user.id,
            body.purpose,
            db,
            plan_tier=body.plan_tier,
            api_id=body.api_id,
            is_super=user.role == UserRole.SUPER_ADMIN,
        )
    except PaymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/intents/{intent_id}/submit-trx", response_model=PaymentIntentOut)
async def submit_trx(
    intent_id: uuid.UUID,
    body: SubmitTrxRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentTransaction:
    try:
        return await payments.submit_trx(intent_id, user.id, body.trx_id, db)
    except PaymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/mine", response_model=list[PaymentIntentOut])
async def my_transactions(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PaymentTransaction]:
    result = await db.execute(
        select(PaymentTransaction)
        .where(PaymentTransaction.user_id == user.id)
        .order_by(PaymentTransaction.created_at.desc())
    )
    return list(result.scalars().all())
