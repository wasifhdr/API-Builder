import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_column

# WalletLedger.bucket values
BUCKET_BALANCE = "balance"
BUCKET_EARNINGS = "earnings"

# WalletLedger.reason values
REASON_RECHARGE = "recharge"
REASON_SUBSCRIPTION = "subscription"
REASON_API_ACCESS = "api_access"
REASON_CALL_DEBIT = "call_debit"
REASON_CALL_REFUND = "call_refund"
REASON_CALL_EARNING = "call_earning"
REASON_PLATFORM_CUT = "platform_cut"
REASON_SWEEP_OUT = "sweep_out"
REASON_SWEEP_IN = "sweep_in"
REASON_CASHOUT = "cashout"
REASON_ADMIN_ADJUST = "admin_adjust"


class Wallet(Base):
    __tablename__ = "wallets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    balance_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    earnings_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class WalletLedger(Base):
    __tablename__ = "wallet_ledger"
    # Append-only audit trail — the wallet's balance/earnings columns are the
    # fast cache, this table is the source of truth. user_id is nullable
    # because platform_cut rows (Phase W3) belong to the platform, not a user.
    __table_args__ = (Index("ix_wallet_ledger_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bucket: Mapped[str] = mapped_column(String(10))
    amount_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(20))
    balance_after_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_executions.id", ondelete="SET NULL"))
    api_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="SET NULL"))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"))
    counterparty_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    cashout_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cashout_requests.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)


class CashoutStatus(str, enum.Enum):
    REQUESTED = "requested"
    PAID = "paid"
    REJECTED = "rejected"


class CashoutRequest(Base, TimestampMixin):
    __tablename__ = "cashout_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    payout_msisdn: Mapped[str] = mapped_column(String(20))
    status: Mapped[CashoutStatus] = mapped_column(enum_column(CashoutStatus), default=CashoutStatus.REQUESTED)
    bkash_trx_id: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
