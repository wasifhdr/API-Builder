import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import admin as admin_api
from app.config import settings
from app.models.api import ApiAccessGrant, ApiInvite, ApiVisibility, CustomApi, GrantSource
from app.models.audit import AdminAuditLog
from app.models.billing import PaymentPurpose, PaymentStatus, PaymentTransaction, PlanTier
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.user import User, UserRole
from app.models.workflow import Workflow, WorkflowStatus
from app.schemas.admin import AdminApiUpdate

DHAKA = ZoneInfo(settings.quota_tz)


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


async def _make_workflow(db, owner, *, status=WorkflowStatus.READY, name="wf") -> Workflow:
    workflow = Workflow(user_id=owner.id, name=name, start_url="https://example.com", status=status)
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def _make_api(db, owner, *, workflow=None, visibility=ApiVisibility.PRIVATE, is_active=True) -> CustomApi:
    if workflow is None:
        workflow = await _make_workflow(db, owner)
    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"api-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=visibility,
        is_active=is_active,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


def _dhaka_midday(days_ago: int) -> datetime:
    today_dhaka = datetime.now(DHAKA).date()
    target_day = today_dhaka - timedelta(days=days_ago)
    local_noon = datetime(target_day.year, target_day.month, target_day.day, 12, 0, 0, tzinfo=DHAKA)
    return local_noon.astimezone(timezone.utc)


async def _add_execution(db, api, *, status=ExecutionStatus.SUCCEEDED, days_ago=0) -> ApiExecution:
    execution = ApiExecution(api_id=api.id, status=status, created_at=_dhaka_midday(days_ago))
    db.add(execution)
    await db.commit()
    return execution


async def _audit_actions(db) -> list[str]:
    result = await db.execute(select(AdminAuditLog.action))
    return [row[0] for row in result.all()]


# --- GET /admin/apis: list + search ---


async def test_list_apis_includes_owner_and_execution_count(db, make_user):
    owner = await make_user(email="owner@example.com")
    owner.username = "ownername"
    await db.commit()
    api = await _make_api(db, owner)
    await _add_execution(db, api, days_ago=0)
    await _add_execution(db, api, days_ago=1)

    out = await admin_api.list_admin_apis(search=None, db=db)
    assert len(out) == 1
    assert out[0].id == api.id
    assert out[0].owner_email == "owner@example.com"
    assert out[0].owner_username == "ownername"
    assert out[0].execution_count == 2
    assert out[0].visibility == ApiVisibility.PRIVATE
    assert out[0].is_active is True


async def test_search_apis_by_name_slug_or_owner(db, make_user):
    owner1 = await make_user(email="alice@example.com")
    owner1.username = "alice"
    owner2 = await make_user(email="bob@example.com")
    owner2.username = "bob"
    await db.commit()

    api1 = await _make_api(db, owner1)
    api1.name = "Weather Fetcher"
    await db.commit()
    api2 = await _make_api(db, owner2)
    api2.name = "Stock Ticker"
    await db.commit()

    by_name = await admin_api.list_admin_apis(search="weather", db=db)
    assert [a.id for a in by_name] == [api1.id]

    by_slug = await admin_api.list_admin_apis(search=api2.slug, db=db)
    assert [a.id for a in by_slug] == [api2.id]

    by_owner_email = await admin_api.list_admin_apis(search="alice@example", db=db)
    assert [a.id for a in by_owner_email] == [api1.id]

    by_owner_username = await admin_api.list_admin_apis(search="bob", db=db)
    assert [a.id for a in by_owner_username] == [api2.id]

    no_match = await admin_api.list_admin_apis(search="nonexistent-xyz", db=db)
    assert no_match == []


# --- PATCH /admin/apis/{id}: is_active toggle + audit ---


async def test_patch_api_deactivate_flips_flag_and_audits(db, make_user):
    admin = await _make_super_admin(db)
    owner = await make_user()
    api = await _make_api(db, owner, is_active=True)

    result = await admin_api.update_admin_api(
        api_id=api.id, body=AdminApiUpdate(is_active=False), admin=admin, db=db
    )
    assert result.is_active is False

    refreshed = await db.get(CustomApi, api.id)
    assert refreshed.is_active is False

    actions = await _audit_actions(db)
    assert "api.deactivate" in actions


async def test_patch_api_reactivate_flips_flag_and_audits(db, make_user):
    admin = await _make_super_admin(db)
    owner = await make_user()
    api = await _make_api(db, owner, is_active=False)

    result = await admin_api.update_admin_api(
        api_id=api.id, body=AdminApiUpdate(is_active=True), admin=admin, db=db
    )
    assert result.is_active is True

    actions = await _audit_actions(db)
    assert "api.activate" in actions


async def test_patch_api_no_change_does_not_double_audit(db, make_user):
    admin = await _make_super_admin(db)
    owner = await make_user()
    api = await _make_api(db, owner, is_active=True)

    await admin_api.update_admin_api(api_id=api.id, body=AdminApiUpdate(is_active=True), admin=admin, db=db)

    actions = await _audit_actions(db)
    assert "api.activate" not in actions
    assert "api.deactivate" not in actions


# --- DELETE /admin/apis/{id}: cascade + audit ---


async def test_delete_api_cascades_executions_grants_invites_and_audits(db, make_user):
    admin = await _make_super_admin(db)
    owner = await make_user()
    consumer = await make_user()
    api = await _make_api(db, owner)

    execution = await _add_execution(db, api)

    invite = ApiInvite(api_id=api.id, created_by=owner.id, token=secrets.token_urlsafe(24))
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    grant = ApiAccessGrant(api_id=api.id, user_id=consumer.id, granted_via=GrantSource.INVITE, invite_id=invite.id)
    db.add(grant)
    await db.commit()
    await db.refresh(grant)

    api_id, execution_id, invite_id, grant_id = api.id, execution.id, invite.id, grant.id

    result = await admin_api.delete_admin_api(api_id=api_id, admin=admin, db=db)
    assert result == {"ok": True}
    db.expunge_all()

    assert await db.get(CustomApi, api_id) is None
    assert await db.get(ApiExecution, execution_id) is None
    assert await db.get(ApiInvite, invite_id) is None
    assert await db.get(ApiAccessGrant, grant_id) is None

    actions = await _audit_actions(db)
    assert "api.delete" in actions

    log_row = (
        await db.execute(
            select(AdminAuditLog).where(AdminAuditLog.action == "api.delete", AdminAuditLog.target_id == str(api_id))
        )
    ).scalar_one()
    assert log_row.detail["name"] == "Test API"
    assert log_row.detail["slug"] == api.slug
    assert log_row.detail["owner_email"] == owner.email


async def test_delete_api_missing_returns_404(db, make_user):
    admin = await _make_super_admin(db)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.delete_admin_api(api_id=uuid.uuid4(), admin=admin, db=db)
    assert exc_info.value.status_code == 404


# --- GET /admin/users/{id}/workflows ---


async def test_list_user_workflows(db, make_user):
    owner = await make_user()
    wf1 = await _make_workflow(db, owner, name="First")
    wf2 = await _make_workflow(db, owner, name="Second")

    out = await admin_api.list_user_workflows(user_id=owner.id, db=db)
    assert {w.id for w in out} == {wf1.id, wf2.id}


async def test_list_workflows_missing_user_404(db):
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.list_user_workflows(user_id=uuid.uuid4(), db=db)
    assert exc_info.value.status_code == 404


# --- DELETE /admin/workflows/{id}: cascades a published API + audits ---


async def test_delete_workflow_without_api_audits(db, make_user):
    admin = await _make_super_admin(db)
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    workflow_id = workflow.id

    result = await admin_api.delete_admin_workflow(workflow_id=workflow_id, admin=admin, db=db)
    assert result == {"ok": True}
    db.expunge_all()

    assert await db.get(Workflow, workflow_id) is None
    actions = await _audit_actions(db)
    assert "workflow.delete" in actions


async def test_delete_workflow_with_published_api_cascades(db, make_user):
    """custom_apis.workflow_id is ON DELETE CASCADE, so deleting a workflow
    that already has a published API takes the API down with it (documented
    choice in api/admin.py::delete_admin_workflow)."""
    admin = await _make_super_admin(db)
    owner = await make_user()
    workflow = await _make_workflow(db, owner)
    api = await _make_api(db, owner, workflow=workflow)
    workflow_id, api_id = workflow.id, api.id

    result = await admin_api.delete_admin_workflow(workflow_id=workflow_id, admin=admin, db=db)
    assert result == {"ok": True}
    db.expunge_all()

    assert await db.get(Workflow, workflow_id) is None
    assert await db.get(CustomApi, api_id) is None

    actions = await _audit_actions(db)
    assert "workflow.delete" in actions


async def test_delete_workflow_missing_404(db, make_user):
    admin = await _make_super_admin(db)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.delete_admin_workflow(workflow_id=uuid.uuid4(), admin=admin, db=db)
    assert exc_info.value.status_code == 404


# --- GET /admin/stats ---


async def test_stats_zero_state(db):
    stats = await admin_api.get_admin_stats(db=db)
    assert stats.total_users == 0
    assert stats.new_users_7d == 0
    assert stats.suspended_users == 0
    assert stats.total_apis == 0
    assert stats.active_apis == 0
    assert len(stats.executions_by_day) == 14
    assert all(d.total == 0 and d.succeeded == 0 for d in stats.executions_by_day)
    assert stats.success_rate_7d == 0
    assert stats.revenue_verified_bdt == Decimal("0")
    assert stats.pending_payments == 0


async def test_stats_user_counts(db, make_user):
    await make_user()
    recent = await make_user()
    suspended = await make_user()
    suspended.suspended_at = datetime.now(timezone.utc)
    await db.commit()

    # Force one user to look "old" so new_users_7d only counts the fresh ones.
    old = await make_user()
    old.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    await db.commit()

    stats = await admin_api.get_admin_stats(db=db)
    assert stats.total_users == 4
    assert stats.suspended_users == 1
    # `recent`, `suspended`, and the first user were all just created (within 7d).
    assert stats.new_users_7d == 3
    assert recent  # keep reference used


async def test_stats_api_counts(db, make_user):
    owner = await make_user()
    await _make_api(db, owner, is_active=True)
    await _make_api(db, owner, is_active=True)
    await _make_api(db, owner, is_active=False)

    stats = await admin_api.get_admin_stats(db=db)
    assert stats.total_apis == 3
    assert stats.active_apis == 2


async def test_stats_executions_by_day_zero_fill_and_dhaka_bucketing(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner)
    await _add_execution(db, api, status=ExecutionStatus.SUCCEEDED, days_ago=0)
    await _add_execution(db, api, status=ExecutionStatus.FAILED, days_ago=0)
    await _add_execution(db, api, status=ExecutionStatus.SUCCEEDED, days_ago=13)

    stats = await admin_api.get_admin_stats(db=db)
    assert len(stats.executions_by_day) == 14
    dates = [d.date for d in stats.executions_by_day]
    assert dates == sorted(dates)

    assert stats.executions_by_day[-1].total == 2
    assert stats.executions_by_day[-1].succeeded == 1
    assert stats.executions_by_day[0].total == 1
    assert stats.executions_by_day[0].succeeded == 1

    middle_days = stats.executions_by_day[1:-1]
    assert all(d.total == 0 and d.succeeded == 0 for d in middle_days)


async def test_stats_success_rate_7d_across_all_apis(db, make_user):
    owner1 = await make_user()
    owner2 = await make_user()
    api1 = await _make_api(db, owner1)
    api2 = await _make_api(db, owner2)

    await _add_execution(db, api1, status=ExecutionStatus.SUCCEEDED, days_ago=0)
    await _add_execution(db, api1, status=ExecutionStatus.SUCCEEDED, days_ago=1)
    await _add_execution(db, api2, status=ExecutionStatus.FAILED, days_ago=1)
    # Outside the 7d window - must not affect success_rate_7d.
    await _add_execution(db, api2, status=ExecutionStatus.FAILED, days_ago=10)

    stats = await admin_api.get_admin_stats(db=db)
    assert stats.success_rate_7d == pytest.approx(2 / 3)


async def test_stats_revenue_and_pending_payments(db, make_user):
    user = await make_user()

    verified = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.PRO,
        amount_expected_bdt=Decimal("100.00"),
        amount_received_bdt=Decimal("100.00"),
        status=PaymentStatus.VERIFIED,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    verified2 = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.MAX,
        amount_expected_bdt=Decimal("250.00"),
        amount_received_bdt=Decimal("250.00"),
        status=PaymentStatus.VERIFIED,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    pending = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.PRO,
        amount_expected_bdt=Decimal("100.00"),
        status=PaymentStatus.PENDING,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    submitted = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.PRO,
        amount_expected_bdt=Decimal("100.00"),
        status=PaymentStatus.SUBMITTED,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    rejected = PaymentTransaction(
        user_id=user.id,
        purpose=PaymentPurpose.SUBSCRIPTION,
        plan_tier=PlanTier.PRO,
        amount_expected_bdt=Decimal("100.00"),
        status=PaymentStatus.REJECTED,
        bkash_trx_id=f"TRX{uuid.uuid4().hex[:10].upper()}",
    )
    db.add_all([verified, verified2, pending, submitted, rejected])
    await db.commit()

    stats = await admin_api.get_admin_stats(db=db)
    assert stats.revenue_verified_bdt == Decimal("350.00")
    assert stats.pending_payments == 2
