from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_effective_tier
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.user import User, UserRole
from app.models.wallet import REASON_AGENT_DEBIT, REASON_AGENT_REFUND, WalletLedger
from app.services import plans, wallet


class AgentRunNotAllowed(Exception):
    """The user's tier does not permit autonomous authoring."""


async def create_run(user: User, prompt: str, db: AsyncSession) -> AgentRun:
    """Creates an agent run and takes the wallet charge for it.

    Gate and debit are one operation, matching the per-call metering rule: a
    user who can't pay never gets a run row. Super admins bypass both by an
    explicit branch, never by faking a tier. Does NOT commit.
    """
    is_super = user.role == UserRole.SUPER_ADMIN
    tier = await get_effective_tier(user.id, db)

    if not is_super and await plans.agent_runs_per_day(tier, db) <= 0:
        raise AgentRunNotAllowed("Autonomous authoring requires a Pro or Max plan.")

    run = AgentRun(user_id=user.id, prompt=prompt, status=AgentRunStatus.PLANNING)
    db.add(run)
    await db.flush()  # assigns run.id for the ledger row

    if not is_super:
        price = await plans.agent_run_price(tier, db)
        if price > 0:
            # Raises InsufficientBalance, which the caller surfaces as 402.
            await wallet.debit(user.id, price, REASON_AGENT_DEBIT, db, agent_run_id=run.id)

    return run


async def _already_refunded(run: AgentRun, db: AsyncSession) -> bool:
    result = await db.execute(
        select(WalletLedger).where(
            WalletLedger.reason == REASON_AGENT_REFUND,
            WalletLedger.agent_run_id == run.id,
        )
    )
    return result.first() is not None


async def _debit_amount(run: AgentRun, db: AsyncSession):
    result = await db.execute(
        select(WalletLedger).where(
            WalletLedger.reason == REASON_AGENT_DEBIT,
            WalletLedger.agent_run_id == run.id,
        )
    )
    row = result.scalar_one_or_none()
    return None if row is None else -row.amount_bdt


async def finish_run(
    run: AgentRun, *, succeeded: bool, reason: str | None, db: AsyncSession
) -> None:
    """Terminates a run. A failed run is refunded in full — the user received
    nothing, so charging for it would be charging for the system's inability to
    do the job. Idempotent: a second call never double-refunds. Does NOT commit.
    """
    if run.status in (AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED):
        return

    run.status = AgentRunStatus.SUCCEEDED if succeeded else AgentRunStatus.FAILED
    run.failure_reason = reason

    if succeeded:
        return

    if await _already_refunded(run, db):
        return
    price = await _debit_amount(run, db)
    if price is None or price <= 0:
        return
    await wallet.credit(run.user_id, price, REASON_AGENT_REFUND, db, agent_run_id=run.id)
