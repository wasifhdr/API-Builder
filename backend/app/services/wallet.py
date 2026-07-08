import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_effective_tier
from app.models.wallet import (
    BUCKET_BALANCE,
    BUCKET_EARNINGS,
    REASON_CALL_EARNING,
    REASON_CASHOUT,
    REASON_PLATFORM_CUT,
    REASON_SWEEP_IN,
    REASON_SWEEP_OUT,
    CashoutRequest,
    Wallet,
    WalletLedger,
)
from app.services.plans import plan_for

_BUCKET_ATTR = {BUCKET_BALANCE: "balance_bdt", BUCKET_EARNINGS: "earnings_bdt"}


class InsufficientBalance(Exception):
    def __init__(self, needed: Decimal, available: Decimal):
        self.needed = needed
        self.available = available
        super().__init__(f"insufficient balance: need {needed}, have {available}")


async def get_or_create(user_id: uuid.UUID, db: AsyncSession) -> Wallet:
    wallet = await db.get(Wallet, user_id)
    if wallet is not None:
        return wallet
    stmt = pg_insert(Wallet).values(user_id=user_id).on_conflict_do_nothing(index_elements=[Wallet.user_id])
    await db.execute(stmt)
    wallet = await db.get(Wallet, user_id)
    assert wallet is not None
    return wallet


async def balances(user_id: uuid.UUID, db: AsyncSession) -> tuple[Decimal, Decimal]:
    wallet = await db.get(Wallet, user_id)
    if wallet is None:
        return Decimal("0"), Decimal("0")
    return wallet.balance_bdt, wallet.earnings_bdt


def _ledger_row(
    user_id: uuid.UUID | None,
    bucket: str,
    signed_amount: Decimal,
    reason: str,
    balance_after: Decimal,
    *,
    execution_id: uuid.UUID | None,
    api_id: uuid.UUID | None,
    transaction_id: uuid.UUID | None,
    counterparty_user_id: uuid.UUID | None,
    cashout_request_id: uuid.UUID | None = None,
) -> WalletLedger:
    return WalletLedger(
        user_id=user_id,
        bucket=bucket,
        amount_bdt=signed_amount,
        reason=reason,
        balance_after_bdt=balance_after,
        execution_id=execution_id,
        api_id=api_id,
        transaction_id=transaction_id,
        counterparty_user_id=counterparty_user_id,
        cashout_request_id=cashout_request_id,
    )


async def debit(
    user_id: uuid.UUID,
    amount: Decimal,
    reason: str,
    db: AsyncSession,
    *,
    bucket: str = BUCKET_BALANCE,
    execution_id: uuid.UUID | None = None,
    api_id: uuid.UUID | None = None,
    transaction_id: uuid.UUID | None = None,
    counterparty_user_id: uuid.UUID | None = None,
    cashout_request_id: uuid.UUID | None = None,
) -> Decimal:
    """Atomically debits `amount` from user_id's `bucket` via a conditional
    UPDATE — never a read-then-write — so concurrent debits can never drive
    the bucket negative. Raises InsufficientBalance (leaving the balance
    untouched) if the bucket can't cover it, including when no wallet row
    exists yet. Does NOT commit; the caller owns the transaction boundary."""
    attr = _BUCKET_ATTR[bucket]
    column = getattr(Wallet, attr)

    result = await db.execute(
        update(Wallet)
        .where(Wallet.user_id == user_id, column >= amount)
        .values({attr: column - amount})
        .returning(column)
    )
    row = result.first()
    if row is None:
        wallet = await db.get(Wallet, user_id)
        available = getattr(wallet, attr) if wallet is not None else Decimal("0")
        raise InsufficientBalance(needed=amount, available=available)

    new_value = row[0]
    db.add(_ledger_row(
        user_id, bucket, -amount, reason, new_value,
        execution_id=execution_id, api_id=api_id,
        transaction_id=transaction_id, counterparty_user_id=counterparty_user_id,
        cashout_request_id=cashout_request_id,
    ))
    return new_value


async def credit(
    user_id: uuid.UUID,
    amount: Decimal,
    reason: str,
    db: AsyncSession,
    *,
    bucket: str = BUCKET_BALANCE,
    execution_id: uuid.UUID | None = None,
    api_id: uuid.UUID | None = None,
    transaction_id: uuid.UUID | None = None,
    counterparty_user_id: uuid.UUID | None = None,
    cashout_request_id: uuid.UUID | None = None,
) -> Decimal:
    """Atomically credits `amount` to user_id's `bucket`, creating the wallet
    row on first use (INSERT .. ON CONFLICT DO UPDATE — safe under concurrent
    first-ever credits for the same user). Does NOT commit; the caller owns
    the transaction boundary."""
    attr = _BUCKET_ATTR[bucket]
    column = getattr(Wallet, attr)

    stmt = pg_insert(Wallet).values(user_id=user_id, **{attr: amount})
    stmt = stmt.on_conflict_do_update(
        index_elements=[Wallet.user_id],
        set_={attr: column + stmt.excluded[attr]},
    ).returning(column)
    result = await db.execute(stmt)
    new_value = result.scalar_one()

    db.add(_ledger_row(
        user_id, bucket, amount, reason, new_value,
        execution_id=execution_id, api_id=api_id,
        transaction_id=transaction_id, counterparty_user_id=counterparty_user_id,
        cashout_request_id=cashout_request_id,
    ))
    return new_value


async def sweep(user_id: uuid.UUID, amount: Decimal | None, db: AsyncSession) -> Decimal:
    """Moves `amount` (default: all current earnings) from earnings into
    spendable balance. No-op (returns 0) if there's nothing to sweep. Raises
    InsufficientBalance if `amount` exceeds earnings. Does NOT commit."""
    if amount is None:
        _, amount = await balances(user_id, db)
    if amount <= 0:
        return Decimal("0")
    await debit(user_id, amount, REASON_SWEEP_OUT, db, bucket=BUCKET_EARNINGS)
    await credit(user_id, amount, REASON_SWEEP_IN, db, bucket=BUCKET_BALANCE)
    return amount


async def request_cashout(
    user_id: uuid.UUID, amount: Decimal, payout_msisdn: str, db: AsyncSession,
) -> CashoutRequest:
    """Creates a cashout request and immediately holds `amount` out of
    earnings (an ordinary debit) so it can't be double-spent while the
    request is pending admin approval. Raises InsufficientBalance if earnings
    can't cover it. Does NOT commit."""
    cashout = CashoutRequest(user_id=user_id, amount_bdt=amount, payout_msisdn=payout_msisdn)
    db.add(cashout)
    await db.flush()
    await debit(
        user_id, amount, REASON_CASHOUT, db, bucket=BUCKET_EARNINGS, cashout_request_id=cashout.id,
    )
    return cashout


async def split_sale_proceeds(
    owner_id: uuid.UUID,
    price: Decimal,
    db: AsyncSession,
    *,
    api_id: uuid.UUID | None = None,
    execution_id: uuid.UUID | None = None,
    counterparty_user_id: uuid.UUID | None = None,
) -> tuple[Decimal, Decimal]:
    """Splits a `price` already collected from a caller into (owner earning,
    platform cut) based on the owner's current platform_cut_pct, crediting
    the owner's earnings and recording a platform_cut ledger row (skipped if
    the cut rounds to 0). Returns (earning, cut) — the two legs always sum to
    `price` exactly, since earning is the remainder after cut, never rounded
    independently. Shared by per-call settlement (handlers.py) and API
    subscription accept/renew (invites.py). Does NOT commit."""
    owner_tier = await get_effective_tier(owner_id, db)
    cut_pct = (await plan_for(owner_tier, db)).platform_cut_pct
    cut = (price * cut_pct / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    earning = price - cut

    await credit(
        owner_id, earning, REASON_CALL_EARNING, db, bucket=BUCKET_EARNINGS,
        api_id=api_id, execution_id=execution_id, counterparty_user_id=counterparty_user_id,
    )
    if cut > 0:
        db.add(WalletLedger(
            user_id=None, bucket=BUCKET_BALANCE, amount_bdt=cut, reason=REASON_PLATFORM_CUT,
            balance_after_bdt=cut, api_id=api_id, execution_id=execution_id,
            counterparty_user_id=counterparty_user_id,
        ))
    return earning, cut
