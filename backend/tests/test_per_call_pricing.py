from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.public import _price_for_call
from app.models.api import ApiPricingMode, CustomApi
from app.models.billing import PlanTier
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.wallet import REASON_CALL_DEBIT, REASON_RECHARGE, WalletLedger
from app.models.workflow import Workflow
from app.services import wallet
from app.services.wallet import InsufficientBalance
from app.workers.handlers import _settle_call_charge


async def _make_api(db, owner, *, pricing_mode=ApiPricingMode.FREE, price_bdt=None):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-percall-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": [], "extraction": {}},
        pricing_mode=pricing_mode,
        price_bdt=price_bdt,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def _give_pro(db, user):
    from datetime import datetime, timedelta, timezone

    from app.models.billing import Subscription, SubscriptionStatus

    now = datetime.now(timezone.utc)
    db.add(
        Subscription(
            user_id=user.id, tier=PlanTier.PRO, status=SubscriptionStatus.ACTIVE,
            starts_at=now, expires_at=now + timedelta(days=30),
        )
    )
    await db.commit()


# --- _price_for_call: pure pricing decision ---


def test_price_for_call_per_call_mode_charges_non_owner():
    api = CustomApi(pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("5.00"))
    assert _price_for_call(api, is_owner_or_super=False) == Decimal("5.00")


def test_price_for_call_per_call_mode_free_for_owner_or_super():
    api = CustomApi(pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("5.00"))
    assert _price_for_call(api, is_owner_or_super=True) is None


@pytest.mark.parametrize("mode", [ApiPricingMode.FREE, ApiPricingMode.ONE_TIME, ApiPricingMode.SUBSCRIPTION])
def test_price_for_call_non_per_call_modes_never_charge(mode):
    api = CustomApi(pricing_mode=mode, price_bdt=Decimal("5.00"))
    assert _price_for_call(api, is_owner_or_super=False) is None


# --- the atomic charge-at-enqueue sequence used by public.py::run_api ---


async def test_charge_sequence_sufficient_balance_debits_and_keeps_execution(db, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner, pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("5.00"))
    await wallet.credit(caller.id, Decimal("20.00"), REASON_RECHARGE, db)
    await db.commit()

    execution = ApiExecution(
        api_id=api.id, caller_user_id=caller.id, api_key_id=None, params={}, status=ExecutionStatus.QUEUED,
    )
    db.add(execution)
    await db.flush()
    exec_id = execution.id

    await wallet.debit(caller.id, api.price_bdt, REASON_CALL_DEBIT, db, api_id=api.id, execution_id=exec_id)
    await db.commit()

    assert await db.get(ApiExecution, exec_id) is not None
    balance, _ = await wallet.balances(caller.id, db)
    assert balance == Decimal("15.00")


async def test_charge_sequence_insufficient_balance_rolls_back_execution(db, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner, pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("5.00"))

    execution = ApiExecution(
        api_id=api.id, caller_user_id=caller.id, api_key_id=None, params={}, status=ExecutionStatus.QUEUED,
    )
    db.add(execution)
    await db.flush()
    exec_id = execution.id

    with pytest.raises(InsufficientBalance):
        await wallet.debit(caller.id, api.price_bdt, REASON_CALL_DEBIT, db, api_id=api.id, execution_id=exec_id)
    await db.rollback()

    assert await db.get(ApiExecution, exec_id) is None
    ledger_rows = (
        await db.execute(select(WalletLedger).where(WalletLedger.execution_id == exec_id))
    ).scalars().all()
    assert ledger_rows == []


# --- _settle_call_charge: split earnings/cut on success, refund on failure ---


async def test_settle_success_splits_earnings_and_platform_cut(db, make_user):
    owner = await make_user()
    caller = await make_user()
    await _give_pro(db, owner)  # default seed: Pro platform_cut_pct = 25%
    api = await _make_api(db, owner, pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("2.00"))

    execution = ApiExecution(
        api_id=api.id, caller_user_id=caller.id, api_key_id=None, params={}, status=ExecutionStatus.SUCCEEDED,
    )
    db.add(execution)
    await db.flush()
    db.add(WalletLedger(
        user_id=caller.id, bucket="balance", amount_bdt=Decimal("-2.00"), reason=REASON_CALL_DEBIT,
        balance_after_bdt=Decimal("0.00"), api_id=api.id, execution_id=execution.id,
    ))
    await db.commit()

    await _settle_call_charge(execution, api, succeeded=True, db=db)
    await db.commit()

    _, owner_earnings = await wallet.balances(owner.id, db)
    assert owner_earnings == Decimal("1.50")  # 2.00 - 25% cut

    cut_row = (
        await db.execute(
            select(WalletLedger).where(WalletLedger.execution_id == execution.id, WalletLedger.reason == "platform_cut")
        )
    ).scalar_one()
    assert cut_row.user_id is None
    assert cut_row.amount_bdt == Decimal("0.50")

    # the three legs (caller debit, owner earning, platform cut) sum to zero
    all_rows = (
        await db.execute(select(WalletLedger).where(WalletLedger.execution_id == execution.id))
    ).scalars().all()
    assert sum((r.amount_bdt for r in all_rows), Decimal("0")) == Decimal("0")


async def test_settle_success_rounds_cut_with_no_dust(db, make_user):
    owner = await make_user()
    caller = await make_user()
    await _give_pro(db, owner)
    api = await _make_api(db, owner, pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("3.33"))

    execution = ApiExecution(
        api_id=api.id, caller_user_id=caller.id, api_key_id=None, params={}, status=ExecutionStatus.SUCCEEDED,
    )
    db.add(execution)
    await db.flush()
    db.add(WalletLedger(
        user_id=caller.id, bucket="balance", amount_bdt=Decimal("-3.33"), reason=REASON_CALL_DEBIT,
        balance_after_bdt=Decimal("0.00"), api_id=api.id, execution_id=execution.id,
    ))
    await db.commit()

    await _settle_call_charge(execution, api, succeeded=True, db=db)
    await db.commit()

    _, owner_earnings = await wallet.balances(owner.id, db)
    cut_row = (
        await db.execute(
            select(WalletLedger).where(WalletLedger.execution_id == execution.id, WalletLedger.reason == "platform_cut")
        )
    ).scalar_one()
    assert cut_row.amount_bdt == Decimal("0.83")
    assert owner_earnings == Decimal("2.50")
    assert cut_row.amount_bdt + owner_earnings == Decimal("3.33")


async def test_settle_failure_refunds_caller_and_pays_no_one(db, make_user):
    owner = await make_user()
    caller = await make_user()
    await _give_pro(db, owner)
    api = await _make_api(db, owner, pricing_mode=ApiPricingMode.PER_CALL, price_bdt=Decimal("2.00"))

    execution = ApiExecution(
        api_id=api.id, caller_user_id=caller.id, api_key_id=None, params={}, status=ExecutionStatus.FAILED,
    )
    db.add(execution)
    await db.flush()
    db.add(WalletLedger(
        user_id=caller.id, bucket="balance", amount_bdt=Decimal("-2.00"), reason=REASON_CALL_DEBIT,
        balance_after_bdt=Decimal("0.00"), api_id=api.id, execution_id=execution.id,
    ))
    await db.commit()

    await _settle_call_charge(execution, api, succeeded=False, db=db)
    await db.commit()

    caller_balance, _ = await wallet.balances(caller.id, db)
    assert caller_balance == Decimal("2.00")  # refunded in full

    owner_balance, owner_earnings = await wallet.balances(owner.id, db)
    assert owner_balance == Decimal("0")
    assert owner_earnings == Decimal("0")

    cut_rows = (
        await db.execute(
            select(WalletLedger).where(WalletLedger.execution_id == execution.id, WalletLedger.reason == "platform_cut")
        )
    ).scalars().all()
    assert cut_rows == []


async def test_settle_noop_for_free_call(db, make_user):
    owner = await make_user()
    caller = await make_user()
    api = await _make_api(db, owner, pricing_mode=ApiPricingMode.FREE)

    execution = ApiExecution(
        api_id=api.id, caller_user_id=caller.id, api_key_id=None, params={}, status=ExecutionStatus.SUCCEEDED,
    )
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    await _settle_call_charge(execution, api, succeeded=True, db=db)
    await db.commit()

    ledger_rows = (
        await db.execute(select(WalletLedger).where(WalletLedger.execution_id == execution.id))
    ).scalars().all()
    assert ledger_rows == []
