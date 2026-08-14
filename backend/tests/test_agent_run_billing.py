from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.agent_run import AgentRunStatus
from app.models.billing import PlanTier, Subscription, SubscriptionStatus
from app.models.user import UserRole
from app.models.wallet import REASON_AGENT_DEBIT, REASON_AGENT_REFUND, WalletLedger
from app.services import agent_runs, plans, wallet
from app.services.plans import invalidate_cache


@pytest.fixture(autouse=True)
def _reset_plan_cache():
    invalidate_cache()
    yield
    invalidate_cache()


async def _make_pro(db, make_user, amount="100.00"):
    user = await make_user()
    sub = Subscription(
        user_id=user.id,
        tier=PlanTier.PRO,
        status=SubscriptionStatus.ACTIVE,
        starts_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(sub)
    await wallet.credit(user.id, Decimal(amount), "recharge", db)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_create_run_debits_the_wallet(db, make_user):
    user = await _make_pro(db, make_user)

    run = await agent_runs.create_run(user, "search fridges", db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert balance == Decimal("100.00") - price
    assert run.status == AgentRunStatus.PLANNING


@pytest.mark.asyncio
async def test_failed_run_is_refunded_in_full(db, make_user):
    user = await _make_pro(db, make_user)

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=False, reason="gave up", db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")
    assert run.status == AgentRunStatus.FAILED
    assert run.failure_reason == "gave up"


@pytest.mark.asyncio
async def test_succeeded_run_keeps_the_charge(db, make_user):
    user = await _make_pro(db, make_user)

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=True, reason=None, db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert balance == Decimal("100.00") - price
    assert run.status == AgentRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_refund_is_idempotent(db, make_user):
    user = await _make_pro(db, make_user)

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=False, reason="x", db=db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=False, reason="x", db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")
    refunds = await db.execute(
        select(WalletLedger).where(
            WalletLedger.user_id == user.id, WalletLedger.reason == REASON_AGENT_REFUND
        )
    )
    assert len(refunds.scalars().all()) == 1


@pytest.mark.asyncio
async def test_free_tier_is_rejected(db, make_user):
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    with pytest.raises(agent_runs.AgentRunNotAllowed):
        await agent_runs.create_run(user, "p", db)


@pytest.mark.asyncio
async def test_insufficient_balance_raises(db, make_user):
    user = await _make_pro(db, make_user, amount="0.00")

    with pytest.raises(wallet.InsufficientBalance):
        await agent_runs.create_run(user, "p", db)


@pytest.mark.asyncio
async def test_super_admin_is_not_charged_and_bypasses_the_gate(db, make_user):
    user = await make_user()  # free tier, no subscription
    user.role = UserRole.SUPER_ADMIN
    await db.commit()
    await db.refresh(user)

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    debits = await db.execute(
        select(WalletLedger).where(
            WalletLedger.user_id == user.id, WalletLedger.reason == REASON_AGENT_DEBIT
        )
    )
    assert debits.scalars().all() == []
    assert run is not None


@pytest.mark.asyncio
async def test_cancel_run_refunds_and_marks_cancelled(db, make_user):
    user = await _make_pro(db, make_user)
    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    await agent_runs.cancel_run(run, db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert run.status == AgentRunStatus.CANCELLED
    assert balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_cancel_run_refunds_only_once(db, make_user):
    user = await _make_pro(db, make_user)
    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    await agent_runs.cancel_run(run, db)
    await db.commit()
    await agent_runs.cancel_run(run, db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_a_cancelled_run_cannot_later_be_marked_failed(db, make_user):
    """Guards the race between a user cancelling and the runner's own timeout
    path finishing the same run."""
    user = await _make_pro(db, make_user)
    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    await agent_runs.cancel_run(run, db)
    await agent_runs.finish_run(run, succeeded=False, reason="timeout", db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert run.status == AgentRunStatus.CANCELLED
    assert balance == Decimal("100.00")
