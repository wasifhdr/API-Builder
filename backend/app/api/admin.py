import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_effective_tier, require_super_admin
from app.db import get_db
from app.models.api import ApiKey, CustomApi
from app.models.audit import AdminAuditLog
from app.models.billing import (
    BkashSmsReceipt,
    PaymentStatus,
    PaymentTransaction,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    VerificationMethod,
)
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.plan_settings import PlanSettings
from app.models.user import User, UserRole
from app.models.wallet import BUCKET_EARNINGS, REASON_CASHOUT, CashoutRequest, CashoutStatus
from app.models.workflow import Workflow
from app.redis import redis_client
from app.schemas.admin import (
    AdminApiOut,
    AdminApiUpdate,
    AdminAuditLogOut,
    AdminCashoutOut,
    AdminKeyOut,
    AdminKeyUpdate,
    AdminPlanOut,
    AdminPlanUpdate,
    AdminSmsOut,
    AdminStatsDayOut,
    AdminStatsOut,
    AdminSubscriptionOut,
    AdminTransactionOut,
    AdminUserDetailOut,
    AdminUserOut,
    AdminUserUpdate,
    AdminWorkflowOut,
    CashoutPayRequest,
    CashoutRejectRequest,
    RejectRequest,
)
from app.services import plans as plans_service
from app.services import wallet as wallet_service
from app.services.accounts import delete_user
from app.services.audit import log_admin_action
from app.services.sessions import user_sessions_key
from app.services.sms_matcher import apply_verified_effects

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])

ADMIN_OVERRIDE_DAYS = 30


def _dhaka_day(column):
    """Truncates a UTC timestamp column to its Asia/Dhaka calendar day.

    Mirrors `api/apis.py::_dhaka_day` exactly — kept as a local copy rather
    than a shared import since each router owns its own query helpers here.
    """
    return func.date_trunc("day", func.timezone(settings.quota_tz, column))


async def _transaction_out(db: AsyncSession, t: PaymentTransaction) -> AdminTransactionOut:
    user = await db.get(User, t.user_id)
    return AdminTransactionOut(
        id=t.id,
        user_id=t.user_id,
        user_email=user.email,
        user_username=user.username,
        purpose=t.purpose,
        plan_tier=t.plan_tier,
        api_id=t.api_id,
        amount_expected_bdt=t.amount_expected_bdt,
        amount_received_bdt=t.amount_received_bdt,
        bkash_trx_id=t.bkash_trx_id,
        status=t.status,
        verification_method=t.verification_method,
        note=t.note,
        created_at=t.created_at,
    )


@router.get("/transactions", response_model=list[AdminTransactionOut])
async def list_transactions(db: AsyncSession = Depends(get_db)) -> list[AdminTransactionOut]:
    result = await db.execute(
        select(PaymentTransaction, User.email, User.username)
        .join(User, User.id == PaymentTransaction.user_id)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(200)
    )
    return [
        AdminTransactionOut(
            id=t.id,
            user_id=t.user_id,
            user_email=user_email,
            user_username=user_username,
            purpose=t.purpose,
            plan_tier=t.plan_tier,
            api_id=t.api_id,
            amount_expected_bdt=t.amount_expected_bdt,
            amount_received_bdt=t.amount_received_bdt,
            bkash_trx_id=t.bkash_trx_id,
            status=t.status,
            verification_method=t.verification_method,
            note=t.note,
            created_at=t.created_at,
        )
        for t, user_email, user_username in result.all()
    ]


@router.post("/transactions/{transaction_id}/verify", response_model=AdminTransactionOut)
async def verify_transaction(
    transaction_id: uuid.UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTransactionOut:
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
    log_admin_action(
        db, admin, "transaction.verify", "transaction", transaction.id,
        {"amount_received_bdt": str(transaction.amount_received_bdt)},
    )
    await db.commit()
    await db.refresh(transaction)
    return await _transaction_out(db, transaction)


@router.post("/transactions/{transaction_id}/reject", response_model=AdminTransactionOut)
async def reject_transaction(
    transaction_id: uuid.UUID,
    body: RejectRequest,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminTransactionOut:
    transaction = await db.get(PaymentTransaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    transaction.status = PaymentStatus.REJECTED
    transaction.note = body.note
    transaction.verified_by_user_id = admin.id
    log_admin_action(
        db, admin, "transaction.reject", "transaction", transaction.id, {"note": body.note}
    )
    await db.commit()
    await db.refresh(transaction)
    return await _transaction_out(db, transaction)


async def _cashout_out(db: AsyncSession, c: CashoutRequest) -> AdminCashoutOut:
    user = await db.get(User, c.user_id)
    return AdminCashoutOut(
        id=c.id,
        user_id=c.user_id,
        user_email=user.email,
        user_username=user.username,
        amount_bdt=c.amount_bdt,
        payout_msisdn=c.payout_msisdn,
        status=c.status,
        bkash_trx_id=c.bkash_trx_id,
        note=c.note,
        created_at=c.created_at,
        decided_at=c.decided_at,
    )


@router.get("/cashouts", response_model=list[AdminCashoutOut])
async def list_cashouts(db: AsyncSession = Depends(get_db)) -> list[AdminCashoutOut]:
    result = await db.execute(
        select(CashoutRequest, User.email, User.username)
        .join(User, User.id == CashoutRequest.user_id)
        .order_by(
            case((CashoutRequest.status == CashoutStatus.REQUESTED, 0), else_=1),
            CashoutRequest.created_at.desc(),
        )
    )
    return [
        AdminCashoutOut(
            id=c.id,
            user_id=c.user_id,
            user_email=user_email,
            user_username=user_username,
            amount_bdt=c.amount_bdt,
            payout_msisdn=c.payout_msisdn,
            status=c.status,
            bkash_trx_id=c.bkash_trx_id,
            note=c.note,
            created_at=c.created_at,
            decided_at=c.decided_at,
        )
        for c, user_email, user_username in result.all()
    ]


@router.post("/cashouts/{cashout_id}/pay", response_model=AdminCashoutOut)
async def pay_cashout(
    cashout_id: uuid.UUID,
    body: CashoutPayRequest,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminCashoutOut:
    cashout = await db.get(CashoutRequest, cashout_id)
    if cashout is None:
        raise HTTPException(status_code=404, detail="cashout request not found")
    if cashout.status != CashoutStatus.REQUESTED:
        raise HTTPException(status_code=400, detail=f"cannot pay a {cashout.status.value} cashout")

    cashout.status = CashoutStatus.PAID
    cashout.bkash_trx_id = body.bkash_trx_id
    cashout.decided_by_user_id = admin.id
    cashout.decided_at = datetime.now(timezone.utc)
    log_admin_action(
        db, admin, "cashout.pay", "cashout", cashout.id,
        {"amount_bdt": str(cashout.amount_bdt), "bkash_trx_id": body.bkash_trx_id},
    )
    await db.commit()
    await db.refresh(cashout)
    return await _cashout_out(db, cashout)


@router.post("/cashouts/{cashout_id}/reject", response_model=AdminCashoutOut)
async def reject_cashout(
    cashout_id: uuid.UUID,
    body: CashoutRejectRequest,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminCashoutOut:
    cashout = await db.get(CashoutRequest, cashout_id)
    if cashout is None:
        raise HTTPException(status_code=404, detail="cashout request not found")
    if cashout.status != CashoutStatus.REQUESTED:
        raise HTTPException(status_code=400, detail=f"cannot reject a {cashout.status.value} cashout")

    cashout.status = CashoutStatus.REJECTED
    cashout.note = body.note
    cashout.decided_by_user_id = admin.id
    cashout.decided_at = datetime.now(timezone.utc)
    # Return the held amount to the creator's earnings — a rejected request
    # never left the platform, so nothing was ever actually cashed out.
    await wallet_service.credit(
        cashout.user_id, cashout.amount_bdt, REASON_CASHOUT, db,
        bucket=BUCKET_EARNINGS, cashout_request_id=cashout.id,
    )
    log_admin_action(
        db, admin, "cashout.reject", "cashout", cashout.id,
        {"note": body.note, "amount_bdt": str(cashout.amount_bdt)},
    )
    await db.commit()
    await db.refresh(cashout)
    return await _cashout_out(db, cashout)


@router.get("/sms", response_model=list[AdminSmsOut])
async def list_sms(db: AsyncSession = Depends(get_db)) -> list[BkashSmsReceipt]:
    result = await db.execute(
        select(BkashSmsReceipt).order_by(BkashSmsReceipt.received_at.desc()).limit(200)
    )
    return list(result.scalars().all())


async def _counts_by_user(db: AsyncSession, model, user_id_col) -> dict[uuid.UUID, int]:
    result = await db.execute(select(user_id_col, func.count()).group_by(user_id_col))
    return dict(result.all())


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[AdminUserOut]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = list(result.scalars().all())

    workflow_counts = await _counts_by_user(db, Workflow, Workflow.user_id)
    api_counts = await _counts_by_user(db, CustomApi, CustomApi.owner_id)
    key_counts = await _counts_by_user(db, ApiKey, ApiKey.user_id)

    out = []
    for u in users:
        tier = await get_effective_tier(u.id, db)
        out.append(
            AdminUserOut(
                id=u.id,
                email=u.email,
                name=u.name,
                role=u.role,
                effective_tier=tier,
                username=u.username,
                phone=u.phone,
                suspended_at=u.suspended_at,
                workflow_count=workflow_counts.get(u.id, 0),
                api_count=api_counts.get(u.id, 0),
                key_count=key_counts.get(u.id, 0),
            )
        )
    return out


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
async def get_user_detail(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> AdminUserDetailOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    tier = await get_effective_tier(user_id, db)

    workflow_count = (
        await db.execute(select(func.count()).where(Workflow.user_id == user_id))
    ).scalar_one()
    api_count = (
        await db.execute(select(func.count()).where(CustomApi.owner_id == user_id))
    ).scalar_one()
    key_count = (
        await db.execute(select(func.count()).where(ApiKey.user_id == user_id))
    ).scalar_one()

    now = datetime.now(timezone.utc)
    sub_result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expires_at > now,
        )
    )
    sub = sub_result.scalar_one_or_none()

    return AdminUserDetailOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        effective_tier=tier,
        username=user.username,
        phone=user.phone,
        suspended_at=user.suspended_at,
        workflow_count=workflow_count,
        api_count=api_count,
        key_count=key_count,
        created_at=user.created_at,
        has_password=user.has_password,
        has_google=user.has_google,
        subscription=AdminSubscriptionOut.model_validate(sub) if sub is not None else None,
    )


async def _is_last_super_admin(db: AsyncSession, target: User) -> bool:
    if target.role != UserRole.SUPER_ADMIN:
        return False
    count = (
        await db.execute(select(func.count()).where(User.role == UserRole.SUPER_ADMIN))
    ).scalar_one()
    return count == 1


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    data = body.model_dump(exclude_unset=True)
    is_self = user.id == admin.id

    if ("role" in data or "suspended" in data) and is_self:
        raise HTTPException(status_code=403, detail="you cannot change your own role or suspension status")

    if await _is_last_super_admin(db, user):
        if "role" in data and data["role"] != UserRole.SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="cannot demote the last remaining super admin")
        if data.get("suspended") is True:
            raise HTTPException(status_code=403, detail="cannot suspend the last remaining super admin")

    # --- tier override (byte-for-byte existing behavior) ---
    if "tier" in data:
        result = await db.execute(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.status = SubscriptionStatus.CANCELLED

        if data["tier"] != PlanTier.FREE:
            now = datetime.now(timezone.utc)
            db.add(
                Subscription(
                    user_id=user_id,
                    tier=data["tier"],
                    status=SubscriptionStatus.ACTIVE,
                    starts_at=now,
                    expires_at=now + timedelta(days=ADMIN_OVERRIDE_DAYS),
                )
            )
        log_admin_action(
            db, admin, "subscription.override", "user", user_id, {"tier": data["tier"].value}
        )

    # --- name / phone / role / suspended ---
    update_detail: dict = {}

    if "name" in data and data["name"] != user.name:
        update_detail["name"] = {"old": user.name, "new": data["name"]}
        user.name = data["name"]
    if "phone" in data and data["phone"] != user.phone:
        update_detail["phone"] = {"old": user.phone, "new": data["phone"]}
        user.phone = data["phone"]

    if update_detail:
        log_admin_action(db, admin, "user.update", "user", user_id, update_detail)

    if "role" in data and data["role"] != user.role:
        old_role = user.role
        user.role = data["role"]
        action = "role.promote" if data["role"] == UserRole.SUPER_ADMIN else "role.demote"
        log_admin_action(
            db, admin, action, "user", user_id,
            {"role": {"old": old_role.value, "new": data["role"].value}},
        )

    if "suspended" in data:
        if data["suspended"] and user.suspended_at is None:
            user.suspended_at = datetime.now(timezone.utc)
            log_admin_action(db, admin, "user.suspend", "user", user_id, {})

            sessions_key = user_sessions_key(user.id)
            sids = await redis_client.smembers(sessions_key)
            if sids:
                await redis_client.delete(*(f"sess:{sid}" for sid in sids))
            await redis_client.delete(sessions_key)
        elif not data["suspended"] and user.suspended_at is not None:
            user.suspended_at = None
            log_admin_action(db, admin, "user.unsuspend", "user", user_id, {})

    await db.commit()
    await db.refresh(user)

    tier = await get_effective_tier(user_id, db)
    workflow_count = (
        await db.execute(select(func.count()).where(Workflow.user_id == user_id))
    ).scalar_one()
    api_count = (
        await db.execute(select(func.count()).where(CustomApi.owner_id == user_id))
    ).scalar_one()
    key_count = (
        await db.execute(select(func.count()).where(ApiKey.user_id == user_id))
    ).scalar_one()

    return AdminUserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        effective_tier=tier,
        username=user.username,
        phone=user.phone,
        suspended_at=user.suspended_at,
        workflow_count=workflow_count,
        api_count=api_count,
        key_count=key_count,
    )


@router.delete("/users/{user_id}")
async def delete_user_endpoint(
    user_id: uuid.UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    if user.id == admin.id:
        raise HTTPException(status_code=403, detail="you cannot delete your own account here")

    if await _is_last_super_admin(db, user):
        raise HTTPException(status_code=403, detail="cannot delete the last remaining super admin")

    log_admin_action(
        db, admin, "user.delete", "user", user_id,
        {"email": user.email, "username": user.username},
    )
    await db.commit()

    await delete_user(db, user)
    return {"ok": True}


@router.get("/users/{user_id}/keys", response_model=list[AdminKeyOut])
async def list_user_keys(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[ApiKey]:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


@router.patch("/users/{user_id}/keys/{key_id}", response_model=AdminKeyOut)
async def relabel_user_key(
    user_id: uuid.UUID,
    key_id: uuid.UUID,
    body: AdminKeyUpdate,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != user_id:
        raise HTTPException(status_code=404, detail="key not found")

    old_label = key.label
    key.label = body.label
    log_admin_action(
        db, admin, "key.relabel", "api_key", key_id,
        {"label": {"old": old_label, "new": body.label}},
    )
    await db.commit()
    await db.refresh(key)
    return key


@router.delete("/users/{user_id}/keys/{key_id}", response_model=AdminKeyOut)
async def revoke_user_key(
    user_id: uuid.UUID,
    key_id: uuid.UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != user_id:
        raise HTTPException(status_code=404, detail="key not found")

    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        log_admin_action(db, admin, "key.revoke", "api_key", key_id, {})
        await db.commit()
        await db.refresh(key)
    return key


@router.get("/audit-log", response_model=list[AdminAuditLogOut])
async def list_audit_log(
    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)
) -> list[AdminAuditLogOut]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    result = await db.execute(
        select(AdminAuditLog, User.email, User.username)
        .outerjoin(User, User.id == AdminAuditLog.actor_user_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    out = []
    for log, actor_email, actor_username in result.all():
        out.append(
            AdminAuditLogOut(
                id=log.id,
                actor_user_id=log.actor_user_id,
                actor_email=actor_email,
                actor_username=actor_username,
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                detail=log.detail,
                created_at=log.created_at,
            )
        )
    return out


@router.get("/plans", response_model=list[AdminPlanOut])
async def list_plan_settings(db: AsyncSession = Depends(get_db)) -> list[AdminPlanOut]:
    plans = await plans_service.get_plans(db)
    result = await db.execute(select(PlanSettings))
    rows = {row.tier: row for row in result.scalars().all()}

    out = []
    for tier, config in plans.items():
        row = rows.get(tier.value)
        out.append(
            AdminPlanOut(
                tier=tier,
                price_bdt=config.price_bdt,
                daily_creation_limit=config.daily_creation_limit,
                can_share=config.can_share,
                monthly_call_quota=config.monthly_call_quota,
                platform_cut_pct=config.platform_cut_pct,
                can_cashout=config.can_cashout,
                max_invitees_per_api=config.max_invitees_per_api,
                updated_at=row.updated_at if row is not None else datetime.now(timezone.utc),
            )
        )
    return out


@router.patch("/plans/{tier}", response_model=AdminPlanOut)
async def update_plan_settings(
    tier: PlanTier,
    body: AdminPlanUpdate,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPlanOut:
    data = body.model_dump(exclude_unset=True)

    if tier == PlanTier.FREE and "price_bdt" in data and data["price_bdt"] != 0:
        raise HTTPException(status_code=400, detail="free tier price is locked at 0")
    if tier == PlanTier.FREE and data.get("can_cashout"):
        raise HTTPException(status_code=400, detail="free tier cannot cash out")

    row = await db.get(PlanSettings, tier.value)
    if row is None:
        # Seed the row from current effective config (DB or fallback defaults)
        # so a PATCH against an unseeded table (e.g. a fresh dev DB) works.
        current = (await plans_service.get_plans(db))[tier]
        row = PlanSettings(
            tier=tier.value,
            price_bdt=0 if tier == PlanTier.FREE else current.price_bdt,
            daily_creation_limit=current.daily_creation_limit,
            can_share=current.can_share,
            monthly_call_quota=current.monthly_call_quota,
            platform_cut_pct=current.platform_cut_pct,
            can_cashout=current.can_cashout,
            max_invitees_per_api=current.max_invitees_per_api,
        )
        db.add(row)

    changed: dict = {}
    if "price_bdt" in data:
        new_price = 0 if tier == PlanTier.FREE else data["price_bdt"]
        if new_price != row.price_bdt:
            changed["price_bdt"] = {"old": row.price_bdt, "new": new_price}
        row.price_bdt = new_price
    if "daily_creation_limit" in data:
        if data["daily_creation_limit"] != row.daily_creation_limit:
            changed["daily_creation_limit"] = {"old": row.daily_creation_limit, "new": data["daily_creation_limit"]}
        row.daily_creation_limit = data["daily_creation_limit"]
    if "can_share" in data:
        if data["can_share"] != row.can_share:
            changed["can_share"] = {"old": row.can_share, "new": data["can_share"]}
        row.can_share = data["can_share"]
    if "monthly_call_quota" in data:
        if data["monthly_call_quota"] != row.monthly_call_quota:
            changed["monthly_call_quota"] = {"old": row.monthly_call_quota, "new": data["monthly_call_quota"]}
        row.monthly_call_quota = data["monthly_call_quota"]
    if "platform_cut_pct" in data:
        if data["platform_cut_pct"] != row.platform_cut_pct:
            changed["platform_cut_pct"] = {"old": str(row.platform_cut_pct), "new": str(data["platform_cut_pct"])}
        row.platform_cut_pct = data["platform_cut_pct"]
    if "can_cashout" in data:
        new_can_cashout = False if tier == PlanTier.FREE else data["can_cashout"]
        if new_can_cashout != row.can_cashout:
            changed["can_cashout"] = {"old": row.can_cashout, "new": new_can_cashout}
        row.can_cashout = new_can_cashout
    if "max_invitees_per_api" in data:
        if data["max_invitees_per_api"] != row.max_invitees_per_api:
            changed["max_invitees_per_api"] = {"old": row.max_invitees_per_api, "new": data["max_invitees_per_api"]}
        row.max_invitees_per_api = data["max_invitees_per_api"]

    if changed:
        log_admin_action(db, admin, "plan.update", "plan", tier.value, changed)

    await db.commit()
    await db.refresh(row)
    plans_service.invalidate_cache()

    return AdminPlanOut(
        tier=tier,
        price_bdt=row.price_bdt,
        daily_creation_limit=row.daily_creation_limit,
        can_share=row.can_share,
        monthly_call_quota=row.monthly_call_quota,
        platform_cut_pct=row.platform_cut_pct,
        can_cashout=row.can_cashout,
        max_invitees_per_api=row.max_invitees_per_api,
        updated_at=row.updated_at,
    )


# --- T6: moderation & platform stats ---


@router.get("/apis", response_model=list[AdminApiOut])
async def list_admin_apis(
    search: str | None = None, db: AsyncSession = Depends(get_db)
) -> list[AdminApiOut]:
    exec_counts = select(
        ApiExecution.api_id.label("api_id"), func.count().label("execution_count")
    ).group_by(ApiExecution.api_id).subquery()

    query = (
        select(
            CustomApi,
            User.email.label("owner_email"),
            User.username.label("owner_username"),
            func.coalesce(exec_counts.c.execution_count, 0).label("execution_count"),
        )
        .join(User, User.id == CustomApi.owner_id)
        .outerjoin(exec_counts, exec_counts.c.api_id == CustomApi.id)
        .order_by(CustomApi.created_at.desc())
    )

    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            CustomApi.name.ilike(pattern)
            | CustomApi.slug.ilike(pattern)
            | User.email.ilike(pattern)
            | User.username.ilike(pattern)
        )

    result = await db.execute(query)
    out = []
    for api, owner_email, owner_username, execution_count in result.all():
        out.append(
            AdminApiOut(
                id=api.id,
                workflow_id=api.workflow_id,
                owner_id=api.owner_id,
                owner_email=owner_email,
                owner_username=owner_username,
                slug=api.slug,
                name=api.name,
                visibility=api.visibility,
                is_active=api.is_active,
                spec_status=api.spec_status,
                execution_count=execution_count,
                created_at=api.created_at,
            )
        )
    return out


@router.patch("/apis/{api_id}", response_model=AdminApiOut)
async def update_admin_api(
    api_id: uuid.UUID,
    body: AdminApiUpdate,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminApiOut:
    api = await db.get(CustomApi, api_id)
    if api is None:
        raise HTTPException(status_code=404, detail="api not found")

    if body.is_active != api.is_active:
        api.is_active = body.is_active
        action = "api.activate" if body.is_active else "api.deactivate"
        log_admin_action(db, admin, action, "api", api_id, {"is_active": body.is_active})

    await db.commit()
    await db.refresh(api)

    owner = await db.get(User, api.owner_id)
    execution_count = (
        await db.execute(select(func.count()).where(ApiExecution.api_id == api.id))
    ).scalar_one()

    return AdminApiOut(
        id=api.id,
        workflow_id=api.workflow_id,
        owner_id=api.owner_id,
        owner_email=owner.email,
        owner_username=owner.username,
        slug=api.slug,
        name=api.name,
        visibility=api.visibility,
        is_active=api.is_active,
        spec_status=api.spec_status,
        execution_count=execution_count,
        created_at=api.created_at,
    )


@router.delete("/apis/{api_id}")
async def delete_admin_api(
    api_id: uuid.UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    api = await db.get(CustomApi, api_id)
    if api is None:
        raise HTTPException(status_code=404, detail="api not found")

    owner = await db.get(User, api.owner_id)
    log_admin_action(
        db, admin, "api.delete", "api", api_id,
        {"name": api.name, "slug": api.slug, "owner_email": owner.email if owner else None},
    )
    await db.commit()

    # Core DELETE (not `session.delete`) so ON DELETE CASCADE foreign keys
    # remove api_executions, api_access_grants, and api_invites rows for this
    # API — see services/accounts.py::delete_user for the identical rationale.
    await db.execute(delete(CustomApi).where(CustomApi.id == api_id))
    await db.commit()
    return {"ok": True}


@router.get("/users/{user_id}/workflows", response_model=list[AdminWorkflowOut])
async def list_user_workflows(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[Workflow]:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    result = await db.execute(
        select(Workflow).where(Workflow.user_id == user_id).order_by(Workflow.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/workflows/{workflow_id}")
async def delete_admin_workflow(
    workflow_id: uuid.UUID,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")

    # custom_apis.workflow_id is ON DELETE CASCADE (see models/api.py), so
    # deleting a workflow that already has a published API cascades and takes
    # the API (and, transitively, its executions/grants/invites) with it —
    # matching the DB's own cascade contract rather than blocking. This is a
    # deliberate choice: an admin explicitly deleting a workflow expects it
    # gone, and the FK already encodes "an API cannot outlive its workflow."
    log_admin_action(
        db, admin, "workflow.delete", "workflow", workflow_id,
        {"name": workflow.name, "owner_id": str(workflow.user_id)},
    )
    await db.commit()

    await db.execute(delete(Workflow).where(Workflow.id == workflow_id))
    await db.commit()
    return {"ok": True}


@router.get("/stats", response_model=AdminStatsOut)
async def get_admin_stats(db: AsyncSession = Depends(get_db)) -> AdminStatsOut:
    now = datetime.now(timezone.utc)
    window_7d_start = now - timedelta(days=7)
    tz = ZoneInfo(settings.quota_tz)
    today_dhaka = now.astimezone(tz).date()
    first_day_dhaka = today_dhaka - timedelta(days=13)

    user_row = (
        await db.execute(
            select(
                func.count().label("total_users"),
                func.count(case((User.created_at >= window_7d_start, 1))).label("new_users_7d"),
                func.count(case((User.suspended_at.is_not(None), 1))).label("suspended_users"),
            )
        )
    ).one()

    api_row = (
        await db.execute(
            select(
                func.count().label("total_apis"),
                func.count(case((CustomApi.is_active.is_(True), 1))).label("active_apis"),
            )
        )
    ).one()

    window_row = (
        await db.execute(
            select(
                func.count().label("calls_7d"),
                func.count(case((ApiExecution.status == ExecutionStatus.SUCCEEDED, 1))).label("succeeded_7d"),
            ).where(ApiExecution.created_at >= window_7d_start)
        )
    ).one()
    calls_7d = window_row.calls_7d or 0
    succeeded_7d = window_row.succeeded_7d or 0
    success_rate_7d = (succeeded_7d / calls_7d) if calls_7d else 0.0

    day_bucket = _dhaka_day(ApiExecution.created_at)
    day_rows = (
        await db.execute(
            select(
                day_bucket.label("day"),
                func.count().label("total"),
                func.count(case((ApiExecution.status == ExecutionStatus.SUCCEEDED, 1))).label("succeeded"),
            )
            .where(
                ApiExecution.created_at >= datetime(
                    first_day_dhaka.year, first_day_dhaka.month, first_day_dhaka.day, tzinfo=tz
                ),
            )
            .group_by(day_bucket)
        )
    ).all()
    by_day = {row.day.date(): (row.total, row.succeeded) for row in day_rows}

    executions_by_day: list[AdminStatsDayOut] = []
    for offset in range(14):
        day = first_day_dhaka + timedelta(days=offset)
        total, succeeded = by_day.get(day, (0, 0))
        executions_by_day.append(AdminStatsDayOut(date=day.isoformat(), total=total, succeeded=succeeded))

    payment_row = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (PaymentTransaction.status == PaymentStatus.VERIFIED,
                             PaymentTransaction.amount_received_bdt),
                        )
                    ),
                    0,
                ).label("revenue_verified_bdt"),
                func.count(
                    case(
                        (PaymentTransaction.status.in_(
                            (PaymentStatus.PENDING, PaymentStatus.SUBMITTED)
                        ), 1)
                    )
                ).label("pending_payments"),
            )
        )
    ).one()

    return AdminStatsOut(
        total_users=user_row.total_users or 0,
        new_users_7d=user_row.new_users_7d or 0,
        suspended_users=user_row.suspended_users or 0,
        total_apis=api_row.total_apis or 0,
        active_apis=api_row.active_apis or 0,
        executions_by_day=executions_by_day,
        success_rate_7d=success_rate_7d,
        revenue_verified_bdt=payment_row.revenue_verified_bdt,
        pending_payments=payment_row.pending_payments or 0,
    )
