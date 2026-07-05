from datetime import datetime, timedelta, timezone

from app.core.deps import get_effective_tier
from app.models.billing import PlanTier, Subscription, SubscriptionStatus


async def _make_subscription(db, user_id, tier, status, expires_delta):
    sub = Subscription(
        user_id=user_id,
        tier=tier,
        status=status,
        starts_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + expires_delta,
    )
    db.add(sub)
    await db.commit()
    return sub


async def test_no_subscription_is_free_tier(db, make_user):
    user = await make_user()
    assert await get_effective_tier(user.id, db) == PlanTier.FREE


async def test_active_unexpired_subscription_is_used(db, make_user):
    user = await make_user()
    await _make_subscription(db, user.id, PlanTier.PRO, SubscriptionStatus.ACTIVE, timedelta(days=30))
    assert await get_effective_tier(user.id, db) == PlanTier.PRO


async def test_expired_subscription_falls_back_to_free(db, make_user):
    user = await make_user()
    await _make_subscription(db, user.id, PlanTier.PRO, SubscriptionStatus.ACTIVE, timedelta(days=-1))
    assert await get_effective_tier(user.id, db) == PlanTier.FREE


async def test_cancelled_subscription_falls_back_to_free(db, make_user):
    user = await make_user()
    await _make_subscription(db, user.id, PlanTier.MAX, SubscriptionStatus.CANCELLED, timedelta(days=30))
    assert await get_effective_tier(user.id, db) == PlanTier.FREE
