import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.models.plan_settings import PlanSettings
from app.models.user import User, UserRole
from app.models.workflow import Workflow
from app.redis import redis_client
from app.schemas.admin import (
    AdminAuditLogOut,
    AdminKeyOut,
    AdminKeyUpdate,
    AdminPlanOut,
    AdminPlanUpdate,
    AdminSmsOut,
    AdminSubscriptionOut,
    AdminTransactionOut,
    AdminUserDetailOut,
    AdminUserOut,
    AdminUserUpdate,
    RejectRequest,
)
from app.services import plans as plans_service
from app.services.accounts import delete_user
from app.services.audit import log_admin_action
from app.services.sessions import user_sessions_key
from app.services.sms_matcher import apply_verified_effects

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_super_admin)])

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
    admin: User = Depends(require_super_admin),
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
    log_admin_action(
        db, admin, "transaction.verify", "transaction", transaction.id,
        {"amount_received_bdt": str(transaction.amount_received_bdt)},
    )
    await db.commit()
    await db.refresh(transaction)
    return transaction


@router.post("/transactions/{transaction_id}/reject", response_model=AdminTransactionOut)
async def reject_transaction(
    transaction_id: uuid.UUID,
    body: RejectRequest,
    admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
) -> PaymentTransaction:
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
    return transaction


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
        updated_at=row.updated_at,
    )
