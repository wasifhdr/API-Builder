import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def enum_column(enum_cls: type[enum.Enum]) -> SAEnum:
    # Persist enum .value (lowercase strings) as varchar; keeps raw SQL and partial
    # indexes readable and avoids native PG enum migrations.
    return SAEnum(enum_cls, native_enum=False, length=32,
                  values_callable=lambda e: [m.value for m in e])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
