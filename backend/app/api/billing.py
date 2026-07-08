import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import current_user, get_effective_tier
from app.db import get_db
from app.models.billing import PaymentTransaction, PlanTier
from app.models.user import User, UserRole
from app.models.wallet import REASON_SUBSCRIPTION, CashoutRequest, WalletLedger
from app.schemas.billing import (
    BillingConfigOut,
    CashoutOut,
    CashoutRequestIn,
    CreateIntentRequest,
    PaymentIntentOut,
    PlanOut,
    SubmitTrxRequest,
    SubscribeRequest,
    SubscribeResult,
    SweepRequest,
    SweepResult,
    WalletLedgerEntryOut,
    WalletOut,
)
from app.services import payments, subscriptions, wallet
from app.services.payments import PaymentError
from app.services.plans import get_plans, plan_for
from app.services.wallet import InsufficientBalance

router = APIRouter(prefix="/billing", tags=["billing"])


async def _can_cashout(user: User, db: AsyncSession) -> bool:
    if user.role == UserRole.SUPER_ADMIN:
        return True
    tier = await get_effective_tier(user.id, db)
    return (await plan_for(tier, db)).can_cashout


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
            monthly_call_quota=p.monthly_call_quota,
            platform_cut_pct=p.platform_cut_pct,
            can_cashout=p.can_cashout,
            max_invitees_per_api=p.max_invitees_per_api,
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
        return await payments.create_intent(user.id, body.purpose, db, amount_bdt=body.amount_bdt)
    except PaymentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/subscribe", response_model=SubscribeResult)
async def subscribe(
    body: SubscribeRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscribeResult:
    if user.role == UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="super admins don't need plans")
    if body.plan_tier == PlanTier.FREE:
        raise HTTPException(status_code=400, detail="plan_tier must be pro or max")

    price = Decimal((await plan_for(body.plan_tier, db)).price_bdt)
    try:
        await wallet.debit(user.id, price, REASON_SUBSCRIPTION, db)
    except InsufficientBalance as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "detail": "insufficient wallet balance",
                "shortfall_bdt": str(exc.needed - exc.available),
            },
        ) from exc

    sub = await subscriptions.activate(user.id, body.plan_tier, db)
    await db.commit()
    await db.refresh(sub)
    balance, _ = await wallet.balances(user.id, db)
    return SubscribeResult(tier=sub.tier, expires_at=sub.expires_at, balance_bdt=balance)


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


@router.get("/wallet", response_model=WalletOut)
async def get_wallet(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> WalletOut:
    balance, earnings = await wallet.balances(user.id, db)
    if user.role == UserRole.SUPER_ADMIN:
        can_cashout = True
        platform_cut_pct = Decimal("0")
    else:
        tier = await get_effective_tier(user.id, db)
        plan = await plan_for(tier, db)
        can_cashout = plan.can_cashout
        platform_cut_pct = plan.platform_cut_pct
    return WalletOut(
        balance_bdt=balance, earnings_bdt=earnings, can_cashout=can_cashout,
        platform_cut_pct=platform_cut_pct,
    )


@router.get("/wallet/ledger", response_model=list[WalletLedgerEntryOut])
async def get_wallet_ledger(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WalletLedger]:
    result = await db.execute(
        select(WalletLedger)
        .where(WalletLedger.user_id == user.id)
        .order_by(WalletLedger.created_at.desc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(result.scalars().all())


@router.post("/wallet/sweep", response_model=SweepResult)
async def sweep_wallet(
    body: SweepRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> SweepResult:
    try:
        swept = await wallet.sweep(user.id, body.amount_bdt, db)
    except InsufficientBalance as exc:
        raise HTTPException(
            status_code=402,
            detail={"detail": "insufficient earnings", "shortfall_bdt": str(exc.needed - exc.available)},
        ) from exc
    await db.commit()
    balance, earnings = await wallet.balances(user.id, db)
    return SweepResult(swept_bdt=swept, balance_bdt=balance, earnings_bdt=earnings)


@router.post("/wallet/cashout", response_model=CashoutOut, status_code=201)
async def create_cashout(
    body: CashoutRequestIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> CashoutRequest:
    if not await _can_cashout(user, db):
        raise HTTPException(
            status_code=403, detail="your plan doesn't allow cashing out — sweep to balance instead"
        )

    try:
        cashout = await wallet.request_cashout(user.id, body.amount_bdt, body.payout_msisdn, db)
    except InsufficientBalance as exc:
        raise HTTPException(
            status_code=402,
            detail={"detail": "insufficient earnings", "shortfall_bdt": str(exc.needed - exc.available)},
        ) from exc
    await db.commit()
    await db.refresh(cashout)
    return cashout


@router.get("/wallet/cashouts", response_model=list[CashoutOut])
async def list_my_cashouts(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CashoutRequest]:
    result = await db.execute(
        select(CashoutRequest)
        .where(CashoutRequest.user_id == user.id)
        .order_by(CashoutRequest.created_at.desc())
    )
    return list(result.scalars().all())
