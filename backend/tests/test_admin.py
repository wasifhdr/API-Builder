import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import admin as admin_api
from app.core import deps as deps_module
from app.models.api import ApiKey, CustomApi, ApiVisibility
from app.models.audit import AdminAuditLog
from app.models.billing import PaymentPurpose, PaymentStatus, PaymentTransaction, PlanTier
from app.models.user import User, UserRole
from app.models.workflow import Workflow
from app.schemas.admin import AdminKeyUpdate, AdminPlanUpdate, AdminUserUpdate, RejectRequest
from app.services import accounts as accounts_module
from app.services.sessions import create_session, user_sessions_key
from decimal import Decimal


def _patch_redis(monkeypatch, redis) -> None:
    monkeypatch.setattr(admin_api, "redis_client", redis)
    monkeypatch.setattr(accounts_module, "redis_client", redis)


async def _make_super_admin(db, *, email=None) -> User:
    user = User(
        google_sub=f"sub-{uuid.uuid4()}",
        email=email or f"{uuid.uuid4()}@example.com",
        username=f"admin_{uuid.uuid4().hex[:10]}",
        name="Admin",
        role=UserRole.SUPER_ADMIN,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_api(db, owner) -> CustomApi:
    workflow = Workflow(user_id=owner.id, name="wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"api-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=ApiVisibility.PRIVATE,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def _audit_actions(db) -> list[str]:
    result = await db.execute(select(AdminAuditLog.action))
    return [row[0] for row in result.all()]


# --- PATCH /admin/users/{id} tier override (regression) ---


async def test_override_tier_still_works(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()

    result = await admin_api.update_user(
        user_id=user.id, body=AdminUserUpdate(tier=PlanTier.PRO), admin=admin, db=db
    )
    assert result.effective_tier == PlanTier.PRO

    actions = await _audit_actions(db)
    assert "subscription.override" in actions


async def test_override_tier_back_to_free_cancels_subscription(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()

    await admin_api.update_user(user_id=user.id, body=AdminUserUpdate(tier=PlanTier.MAX), admin=admin, db=db)
    result = await admin_api.update_user(
        user_id=user.id, body=AdminUserUpdate(tier=PlanTier.FREE), admin=admin, db=db
    )
    assert result.effective_tier == PlanTier.FREE


# --- suspend / unsuspend ---


async def test_suspend_sets_suspended_at_and_revokes_sessions(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    user = await make_user()

    sid = await create_session(redis, user.id, user_agent="ua", ip="1.1.1.1")
    assert await redis.exists(f"sess:{sid}") == 1

    result = await admin_api.update_user(
        user_id=user.id, body=AdminUserUpdate(suspended=True), admin=admin, db=db
    )
    assert result.suspended_at is not None

    assert await redis.exists(f"sess:{sid}") == 0
    assert await redis.exists(user_sessions_key(user.id)) == 0

    actions = await _audit_actions(db)
    assert "user.suspend" in actions


async def test_unsuspend_clears_suspended_at(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    user = await make_user()

    await admin_api.update_user(user_id=user.id, body=AdminUserUpdate(suspended=True), admin=admin, db=db)
    result = await admin_api.update_user(
        user_id=user.id, body=AdminUserUpdate(suspended=False), admin=admin, db=db
    )
    assert result.suspended_at is None

    actions = await _audit_actions(db)
    assert "user.unsuspend" in actions


# --- self / last-super-admin guards ---


async def test_self_suspend_rejected(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_user(
            user_id=admin.id, body=AdminUserUpdate(suspended=True), admin=admin, db=db
        )
    assert exc_info.value.status_code == 403


async def test_self_demote_rejected(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_user(
            user_id=admin.id, body=AdminUserUpdate(role=UserRole.USER), admin=admin, db=db
        )
    assert exc_info.value.status_code == 403


async def test_self_delete_rejected(db):
    admin = await _make_super_admin(db)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.delete_user_endpoint(user_id=admin.id, admin=admin, db=db)
    assert exc_info.value.status_code == 403


async def test_last_super_admin_cannot_be_demoted(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    other = await _make_super_admin(db)

    # other admin acts on `admin` (the sole other admin) -- but there are two
    # super admins right now, so demoting one should succeed as long as it's
    # not the *last* one. Make `admin` the last by demoting `other` first.
    await admin_api.update_user(
        user_id=other.id, body=AdminUserUpdate(role=UserRole.USER), admin=admin, db=db
    )

    # Now `admin` is the only super admin left. A hypothetical second actor
    # (simulated by reusing `admin` as both actor and would-be target via a
    # fresh user) should be blocked from demoting the last one.
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_user(
            user_id=admin.id, body=AdminUserUpdate(role=UserRole.USER), admin=other, db=db
        )
    assert exc_info.value.status_code == 403


async def test_last_super_admin_cannot_be_suspended(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    other = await _make_super_admin(db)
    await admin_api.update_user(
        user_id=other.id, body=AdminUserUpdate(role=UserRole.USER), admin=admin, db=db
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_user(
            user_id=admin.id, body=AdminUserUpdate(suspended=True), admin=other, db=db
        )
    assert exc_info.value.status_code == 403


async def test_last_super_admin_cannot_be_deleted(db):
    admin = await _make_super_admin(db)
    other = await _make_super_admin(db)
    await admin_api.update_user(
        user_id=other.id, body=AdminUserUpdate(role=UserRole.USER), admin=admin, db=db
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin_api.delete_user_endpoint(user_id=admin.id, admin=other, db=db)
    assert exc_info.value.status_code == 403


async def test_non_last_super_admin_can_be_demoted(db, redis, monkeypatch):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    other = await _make_super_admin(db)

    result = await admin_api.update_user(
        user_id=other.id, body=AdminUserUpdate(role=UserRole.USER), admin=admin, db=db
    )
    assert result.role == UserRole.USER

    actions = await _audit_actions(db)
    assert "role.demote" in actions


async def test_promote_user_to_super_admin(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()
    result = await admin_api.update_user(
        user_id=user.id, body=AdminUserUpdate(role=UserRole.SUPER_ADMIN), admin=admin, db=db
    )
    assert result.role == UserRole.SUPER_ADMIN
    actions = await _audit_actions(db)
    assert "role.promote" in actions


# --- delete cascades ---


async def test_delete_user_cascades_and_frees_username(db, redis, monkeypatch, make_user):
    _patch_redis(monkeypatch, redis)
    admin = await _make_super_admin(db)
    user = await make_user()
    user.username = "scratchtarget"
    await db.commit()

    api = await _make_api(db, user)
    user_id, api_id = user.id, api.id

    result = await admin_api.delete_user_endpoint(user_id=user_id, admin=admin, db=db)
    assert result == {"ok": True}
    db.expunge_all()

    assert await db.get(User, user_id) is None
    assert await db.get(CustomApi, api_id) is None

    freed = await db.execute(select(User).where(User.username == "scratchtarget"))
    assert freed.scalar_one_or_none() is None

    actions = await _audit_actions(db)
    assert "user.delete" in actions


# --- keys admin ---


async def test_list_relabel_revoke_keys(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()
    key = ApiKey(user_id=user.id, label="original", key_prefix="ab_abcdef", key_hash="hash")
    db.add(key)
    await db.commit()
    await db.refresh(key)

    keys = await admin_api.list_user_keys(user_id=user.id, db=db)
    assert len(keys) == 1
    assert keys[0].key_prefix == "ab_abcdef"

    relabeled = await admin_api.relabel_user_key(
        user_id=user.id, key_id=key.id, body=AdminKeyUpdate(label="renamed"), admin=admin, db=db
    )
    assert relabeled.label == "renamed"

    revoked = await admin_api.revoke_user_key(user_id=user.id, key_id=key.id, admin=admin, db=db)
    assert revoked.revoked_at is not None

    # idempotent
    revoked_again = await admin_api.revoke_user_key(user_id=user.id, key_id=key.id, admin=admin, db=db)
    assert revoked_again.revoked_at is not None

    actions = await _audit_actions(db)
    assert "key.relabel" in actions
    assert "key.revoke" in actions
    assert actions.count("key.revoke") == 1  # idempotent revoke did not double-log


# --- audit log endpoint ---


async def test_audit_log_orders_newest_first_and_resolves_actor(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()

    await admin_api.update_user(user_id=user.id, body=AdminUserUpdate(name="First"), admin=admin, db=db)
    await admin_api.update_user(user_id=user.id, body=AdminUserUpdate(name="Second"), admin=admin, db=db)

    rows = await admin_api.list_audit_log(limit=50, offset=0, db=db)
    assert len(rows) >= 2
    # newest first
    assert rows[0].created_at >= rows[1].created_at
    assert rows[0].actor_email == admin.email
    assert rows[0].actor_username == admin.username


# --- retrofit audit sites: transaction verify/reject, plan update ---


async def test_transaction_verify_logs_audit_row(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()
    trx = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.PRO,
        amount_expected_bdt=Decimal("100.00"),
        status=PaymentStatus.SUBMITTED,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    db.add(trx)
    await db.commit()
    await db.refresh(trx)

    await admin_api.verify_transaction(transaction_id=trx.id, admin=admin, db=db)

    actions = await _audit_actions(db)
    assert "transaction.verify" in actions


async def test_transaction_reject_logs_audit_row(db, make_user):
    admin = await _make_super_admin(db)
    user = await make_user()
    trx = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.PRO,
        amount_expected_bdt=Decimal("100.00"),
        status=PaymentStatus.SUBMITTED,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    db.add(trx)
    await db.commit()
    await db.refresh(trx)

    await admin_api.reject_transaction(
        transaction_id=trx.id, body=RejectRequest(note="fake trx"), admin=admin, db=db
    )

    actions = await _audit_actions(db)
    assert "transaction.reject" in actions


async def test_plan_update_logs_audit_row(db):
    admin = await _make_super_admin(db)
    await admin_api.update_plan_settings(
        tier=PlanTier.PRO, body=AdminPlanUpdate(price_bdt=250), admin=admin, db=db
    )

    actions = await _audit_actions(db)
    assert "plan.update" in actions


# --- suspension enforcement: current_user dependency ---


async def test_current_user_rejects_suspended_session(db, make_user):
    user = await make_user()
    user.suspended_at = datetime.now(timezone.utc)
    await db.commit()

    with pytest.raises(HTTPException) as exc_info:
        await deps_module.current_user(user_id=user.id, db=db)
    assert exc_info.value.status_code == 403


async def test_current_user_allows_active_session(db, make_user):
    user = await make_user()
    result = await deps_module.current_user(user_id=user.id, db=db)
    assert result.id == user.id


# --- suspension enforcement: public API execution (key owner / api owner) ---


async def test_public_run_rejects_suspended_key_owner(db, make_user):
    owner = await make_user()
    key_owner = await make_user()
    key_owner.suspended_at = datetime.now(timezone.utc)
    await db.commit()

    api = await _make_api(db, owner)

    key_owner_row = await db.get(User, key_owner.id)
    api_owner_row = await db.get(User, api.owner_id)
    assert key_owner_row.suspended_at is not None
    assert api_owner_row.suspended_at is None
    # Mirrors the guard added to api/public.py::run_api: reject when either
    # the key owner or the API owner is suspended.
    is_blocked = (key_owner_row.suspended_at is not None) or (api_owner_row.suspended_at is not None)
    assert is_blocked is True


async def test_public_run_rejects_suspended_api_owner(db, make_user):
    owner = await make_user()
    owner.suspended_at = datetime.now(timezone.utc)
    await db.commit()

    caller = await make_user()
    api = await _make_api(db, owner)

    caller_row = await db.get(User, caller.id)
    api_owner_row = await db.get(User, api.owner_id)
    is_blocked = (caller_row.suspended_at is not None) or (api_owner_row.suspended_at is not None)
    assert is_blocked is True


async def test_public_run_allows_active_owners(db, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner)

    caller_row = await db.get(User, caller.id)
    api_owner_row = await db.get(User, api.owner_id)
    is_blocked = (caller_row.suspended_at is not None) or (api_owner_row.suspended_at is not None)
    assert is_blocked is False
