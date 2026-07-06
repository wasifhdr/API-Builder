import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column


class UserRole(str, enum.Enum):
    USER = "user"
    SUPER_ADMIN = "super_admin"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: password-only accounts have no Google identity.
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Nullable until claimed (existing Google users are prompted post-login); stored
    # lowercase and immutable once set, enforced in the API layer, not the DB.
    username: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    picture_url: Mapped[str | None] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole), default=UserRole.USER)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    workflows: Mapped[list["Workflow"]] = relationship(back_populates="owner")  # noqa: F821
    apis: Mapped[list["CustomApi"]] = relationship(back_populates="owner")  # noqa: F821

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def has_google(self) -> bool:
        return self.google_sub is not None
