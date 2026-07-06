import pytest
from fastapi import HTTPException

from app.api import admin as admin_api
from app.api import apis as apis_api
from app.models.api import ApiAccessGrant, ApiVisibility, CustomApi, GrantSource
from app.models.billing import PaymentPurpose, PlanTier
from app.models.plan_settings import PlanSettings
from app.models.user import UserRole
from app.models.workflow import Workflow
from app.schemas.admin import AdminPlanUpdate
from app.schemas.api import CustomApiUpdate
from app.services import payments
from app.services.grants import has_access
from app.services.payments import PaymentError
from app.services.plans import get_plans, invalidate_cache, plan_for


@pytest.fixture(autouse=True)
def _reset_plan_cache():
    invalidate_cache()
    yield
    invalidate_cache()


async def _make_super(db, make_user):
    user = await make_user()
    user.role = UserRole.SUPER_ADMIN
    await db.commit()
    await db.refresh(user)
    return user


async def _make_api(db, owner, *, visibility=ApiVisibility.PRIVATE, price_bdt=None):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        visibility=visibility,
        price_bdt=price_bdt,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


# --- plan_for / get_plans: DB rows vs. fallback defaults ---


async def test_plan_for_returns_db_row_values_when_present(db):
    db.add(PlanSettings(tier="pro", price_bdt=250, daily_creation_limit=99, can_share=False))
    await db.commit()

    config = await plan_for(PlanTier.PRO, db)
    assert config.price_bdt == 250
    assert config.daily_creation_limit == 99
    assert config.can_share is False


async def test_plan_for_falls_back_to_defaults_when_table_empty(db):
    # The test DB is created via Base.metadata.create_all and is NEVER seeded
    # by migrations, so plan_settings starts empty here — plan_for must not
    # explode and must return the hardcoded/env defaults.
    config = await plan_for(PlanTier.FREE, db)
    assert config.price_bdt == 0
    assert config.daily_creation_limit == 5
    assert config.can_share is False

    plans = await get_plans(db)
    assert set(plans.keys()) == {PlanTier.FREE, PlanTier.PRO, PlanTier.MAX}


# --- admin PATCH updates a value, cache invalidation reflects it ---


async def test_admin_patch_updates_value_and_invalidates_cache(db, make_user):
    admin = await _make_super(db, make_user)
    # warm the cache with defaults
    before = await plan_for(PlanTier.PRO, db)
    assert before.price_bdt != 999

    await admin_api.update_plan_settings(
        tier=PlanTier.PRO, body=AdminPlanUpdate(price_bdt=999), admin=admin, db=db
    )

    after = await plan_for(PlanTier.PRO, db)
    assert after.price_bdt == 999


async def test_admin_patch_free_tier_price_rejected(db, make_user):
    admin = await _make_super(db, make_user)
    with pytest.raises(HTTPException) as exc_info:
        await admin_api.update_plan_settings(
            tier=PlanTier.FREE, body=AdminPlanUpdate(price_bdt=10), admin=admin, db=db
        )
    assert exc_info.value.status_code == 400


async def test_admin_patch_free_tier_price_zero_allowed(db, make_user):
    admin = await _make_super(db, make_user)
    result = await admin_api.update_plan_settings(
        tier=PlanTier.FREE, body=AdminPlanUpdate(price_bdt=0), admin=admin, db=db
    )
    assert result.price_bdt == 0


async def test_admin_list_plans_returns_all_three(db):
    out = await admin_api.list_plan_settings(db)
    tiers = {p.tier for p in out}
    assert tiers == {PlanTier.FREE, PlanTier.PRO, PlanTier.MAX}


# --- super-admin quota bypass ---


async def test_super_admin_recordings_bypass_quota(db, redis, make_user, monkeypatch):
    from app.api import recordings as recordings_api
    from app.core.deps import UserWithTier
    from app.schemas.recording import CreateRecordingRequest

    monkeypatch.setattr(recordings_api, "redis_client", redis)

    super_user = await _make_super(db, make_user)
    # free tier daily_creation_limit is 5 by default; create well past that
    # and confirm no QuotaExceeded is ever raised because supers skip the
    # quota check entirely.
    ctx = UserWithTier(user=super_user, tier=PlanTier.FREE)
    assert ctx.is_super is True

    for i in range(7):
        resp = await recordings_api.create_recording(
            CreateRecordingRequest(name=f"wf-{i}", start_url="https://example.com"),
            ctx=ctx,
            db=db,
        )
        assert resp.workflow_id is not None


# --- super-admin share-gate bypass (owner is super admin, FREE tier) ---


async def test_super_admin_owner_share_gate_bypassed(db, make_user):
    super_owner = await _make_super(db, make_user)
    grantee = await make_user()
    api = await _make_api(db, super_owner, visibility=ApiVisibility.SHARED)
    # super_owner never subscribed -> free tier -> would normally fail can_share
    db.add(ApiAccessGrant(api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE))
    await db.commit()

    assert await has_access(api, grantee.id, db) is True


async def test_super_admin_update_api_visibility_without_pro(db, make_user):
    super_owner = await _make_super(db, make_user)
    api = await _make_api(db, super_owner)

    updated = await apis_api.update_api(
        api_id=api.id, body=CustomApiUpdate(visibility=ApiVisibility.SHARED), user=super_owner, db=db
    )
    assert updated.visibility == ApiVisibility.SHARED


async def test_super_admin_create_invite_without_pro(db, make_user):
    from app.schemas.invite import CreateInviteRequest

    super_owner = await _make_super(db, make_user)
    api = await _make_api(db, super_owner, visibility=ApiVisibility.SHARED)

    invite = await apis_api.create_invite(api_id=api.id, body=CreateInviteRequest(), user=super_owner, db=db)
    assert invite.api_id == api.id


# --- subscription intent from super rejected ---


async def test_subscription_intent_from_super_rejected(db, make_user):
    super_user = await _make_super(db, make_user)

    with pytest.raises(PaymentError) as exc_info:
        await payments.create_intent(
            super_user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO, is_super=True
        )
    assert exc_info.value.status_code == 400
    assert "super admin" in exc_info.value.detail


async def test_subscription_intent_from_regular_user_still_allowed(db, make_user):
    user = await make_user()
    intent = await payments.create_intent(
        user.id, PaymentPurpose.SUBSCRIPTION, db, plan_tier=PlanTier.PRO, is_super=False
    )
    assert intent.plan_tier == PlanTier.PRO
