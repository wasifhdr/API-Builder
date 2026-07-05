import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_column


class PlanTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    MAX = "max"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    # Users with no ACTIVE row are FREE tier. Renewal of the same tier extends
    # expires_at; upgrades cancel the old row and insert a new one (no proration).
    __table_args__ = (
        Index("uq_one_active_sub_per_user", "user_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tier: Mapped[PlanTier] = mapped_column(enum_column(PlanTier))
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column(SubscriptionStatus), default=SubscriptionStatus.ACTIVE)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"))


class PaymentPurpose(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    API_ACCESS = "api_access"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"


class VerificationMethod(str, enum.Enum):
    AUTO_SMS = "auto_sms"
    MANUAL_ADMIN = "manual_admin"


class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[PaymentPurpose] = mapped_column(enum_column(PaymentPurpose))
    plan_tier: Mapped[PlanTier | None] = mapped_column(enum_column(PlanTier))
    api_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="SET NULL"))
    amount_expected_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    amount_received_bdt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    bkash_trx_id: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        enum_column(PaymentStatus), default=PaymentStatus.PENDING, index=True)
    matched_sms_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bkash_sms_receipts.id", ondelete="SET NULL"))
    verification_method: Mapped[VerificationMethod | None] = mapped_column(
        enum_column(VerificationMethod))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)


class BkashSmsReceipt(Base):
    __tablename__ = "bkash_sms_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_text: Mapped[str] = mapped_column(Text)
    sms_sender: Mapped[str | None] = mapped_column(String(40))
    dedupe_hash: Mapped[str] = mapped_column(String(64), unique=True)
    parsed_trx_id: Mapped[str | None] = mapped_column(String(40), index=True)
    parsed_amount_bdt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    parsed_sender_msisdn: Mapped[str | None] = mapped_column(String(20))
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"), index=True)
