import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.config import settings
from app.db import async_session
from app.models.api import ApiAccessGrant
from app.models.billing import PaymentStatus, PaymentTransaction, Subscription, SubscriptionStatus
from app.models.execution import ApiExecution

log = logging.getLogger("worker")

SWEEP_INTERVAL_SECONDS = 600
EXECUTION_RETENTION_PER_API = 200
FAILURE_ARTIFACT_MAX_AGE_DAYS = 30


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

        # Subscription-mode API grants (Phase W6) carry an expires_at.
        # has_access already denies a call the instant expires_at passes, so
        # this is purely bookkeeping — it makes the owner's Grants list show
        # "revoked" instead of a stale-looking active grant.
        result = await db.execute(
            select(ApiAccessGrant).where(
                ApiAccessGrant.expires_at.is_not(None),
                ApiAccessGrant.expires_at <= now,
                ApiAccessGrant.revoked_at.is_(None),
            )
        )
        lapsed_grants = list(result.scalars())
        for g in lapsed_grants:
            g.revoked_at = now

        await db.commit()

        if expired_subs or expired_intents or lapsed_grants:
            log.info(
                "sweep: expired %d subscriptions, %d payment intents, %d api grants",
                len(expired_subs), len(expired_intents), len(lapsed_grants),
            )

        # Execution-log retention: keep only the most recent N rows per API,
        # oldest first — the DELETE ... WHERE id IN (SELECT ... ROW_NUMBER())
        # pattern avoids needing to know per-api counts up front.
        retention_result = await db.execute(
            text("""
                DELETE FROM api_executions
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY api_id ORDER BY created_at DESC
                        ) AS rn
                        FROM api_executions
                    ) ranked
                    WHERE rn > :limit
                )
            """),
            {"limit": EXECUTION_RETENTION_PER_API},
        )
        deleted_executions = retention_result.rowcount or 0

        live_ids_result = await db.execute(
            select(ApiExecution.id).where(ApiExecution.failure_artifact_path.is_not(None))
        )
        live_ids = {str(row) for row in live_ids_result.scalars()}

        await db.commit()

        if deleted_executions:
            log.info("sweep: deleted %d executions past the per-api retention limit", deleted_executions)

        gc_count = _gc_failure_artifacts(live_ids)
        if gc_count:
            log.info("sweep: garbage-collected %d failure artifact directories", gc_count)


def _gc_failure_artifacts(live_execution_ids: set[str]) -> int:
    """Removes failure-artifact directories that either have no matching
    (still-retained) execution row anymore, or have simply aged out past
    FAILURE_ARTIFACT_MAX_AGE_DAYS regardless of whether the row survives."""
    root = settings.failures_path
    if not root.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=FAILURE_ARTIFACT_MAX_AGE_DAYS)
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        is_orphan = entry.name not in live_execution_ids
        is_old = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc) < cutoff
        if is_orphan or is_old:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


async def periodic_sweep() -> None:
    while True:
        try:
            await sweep_once()
        except Exception:
            log.exception("periodic sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
