import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PlanTier, Subscription, SubscriptionStatus

SUBSCRIPTION_DAYS = 30


async def activate(
    user_id: uuid.UUID,
    tier: PlanTier,
    db: AsyncSession,
    *,
    source_transaction_id: uuid.UUID | None = None,
) -> Subscription:
    """Activates or extends a user's subscription to `tier`. Same tier while
    active -> extends expiry by 30 days. Different tier -> cancels the old
    active row (no proration, per the Blueprint) and starts a fresh 30-day
    period. Does not commit — caller owns the transaction boundary."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == SubscriptionStatus.ACTIVE,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None and existing.tier == tier:
        existing.expires_at = existing.expires_at + timedelta(days=SUBSCRIPTION_DAYS)
        return existing

    if existing is not None:
        existing.status = SubscriptionStatus.CANCELLED

    sub = Subscription(
        user_id=user_id,
        tier=tier,
        status=SubscriptionStatus.ACTIVE,
        starts_at=now,
        expires_at=now + timedelta(days=SUBSCRIPTION_DAYS),
        source_transaction_id=source_transaction_id,
    )
    db.add(sub)
    return sub
