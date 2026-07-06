from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlanSettings(Base):
    __tablename__ = "plan_settings"

    tier: Mapped[str] = mapped_column(String(10), primary_key=True)  # free|pro|max
    price_bdt: Mapped[int] = mapped_column(Integer)
    daily_creation_limit: Mapped[int | None] = mapped_column(Integer)  # None = unlimited
    can_share: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
