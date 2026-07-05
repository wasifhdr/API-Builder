import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column


class ApiVisibility(str, enum.Enum):
    PRIVATE = "private"
    SHARED = "shared"


class SpecStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class CustomApi(Base, TimestampMixin):
    __tablename__ = "custom_apis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[ApiVisibility] = mapped_column(
        enum_column(ApiVisibility), default=ApiVisibility.PRIVATE)
    price_bdt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    workflow_snapshot: Mapped[dict] = mapped_column(JSONB)
    openapi_spec: Mapped[dict | None] = mapped_column(JSONB)
    spec_status: Mapped[SpecStatus] = mapped_column(enum_column(SpecStatus), default=SpecStatus.PENDING)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    owner: Mapped["User"] = relationship(back_populates="apis")  # noqa: F821


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    # Keys belong to a consumer (any user) and are global; what a key may call is
    # decided by api_access_grants + ownership at request time.

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(100), default="default")
    key_prefix: Mapped[str] = mapped_column(String(12), index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrantSource(str, enum.Enum):
    INVITE = "invite"
    PURCHASE = "purchase"
    ADMIN = "admin"


class ApiAccessGrant(Base, TimestampMixin):
    __tablename__ = "api_access_grants"
    __table_args__ = (UniqueConstraint("api_id", "user_id", name="uq_grant_api_user"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_via: Mapped[GrantSource] = mapped_column(enum_column(GrantSource))
    invite_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_invites.id", ondelete="SET NULL"))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiInvite(Base, TimestampMixin):
    __tablename__ = "api_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
