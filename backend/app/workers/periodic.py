import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.db import async_session
from app.models.billing import PaymentStatus, PaymentTransaction, Subscription, SubscriptionStatus

log = logging.getLogger("worker")

SWEEP_INTERVAL_SECONDS = 600


async def sweep_once() -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as db:
        result = await db.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.expires_at <= now,
            )
        )
        expired_subs = list(result.scalars())
        for sub in expired_subs:
            sub.status = SubscriptionStatus.EXPIRED

        # Only PENDING intents expire on a timer — once a TrxID is submitted,
        # it's real money already sent, so it stays open for admin review
        # rather than silently expiring out from under the user.
        cutoff = now - timedelta(hours=settings.payment_intent_ttl_hours)
        result = await db.execute(
            select(PaymentTransaction).where(
                PaymentTransaction.status == PaymentStatus.PENDING,
                PaymentTransaction.created_at <= cutoff,
            )
        )
        expired_intents = list(result.scalars())
        for tx in expired_intents:
            tx.status = PaymentStatus.EXPIRED

        await db.commit()

        if expired_subs or expired_intents:
            log.info(
                "sweep: expired %d subscriptions, %d payment intents",
                len(expired_subs), len(expired_intents),
            )


async def periodic_sweep() -> None:
    while True:
        try:
            await sweep_once()
        except Exception:
            log.exception("periodic sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
