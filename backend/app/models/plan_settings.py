from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlanSettings(Base):
    __tablename__ = "plan_settings"

    tier: Mapped[str] = mapped_column(String(10), primary_key=True)  # free|pro|max
    price_bdt: Mapped[int] = mapped_column(Integer)
    daily_creation_limit: Mapped[int | None] = mapped_column(Integer)  # None = unlimited
    can_share: Mapped[bool] = mapped_column(Boolean)
    monthly_call_quota: Mapped[int | None] = mapped_column(Integer)  # None = unlimited
    # Defaults (0 / False) keep any construction site that omits these two
    # NOT NULL columns (e.g. admin.py's unseeded-tier fallback row) safe.
    platform_cut_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    can_cashout: Mapped[bool] = mapped_column(Boolean, default=False)
    max_invitees_per_api: Mapped[int | None] = mapped_column(Integer)  # None = unlimited
    # Autonomous authoring: 0 runs/day disables the feature for a tier, which
    # is how Free is gated. Price is charged per attempt and refunded when the
    # run ends failed. Same NOT NULL-with-default safety as platform_cut_pct /
    # can_cashout above.
    agent_runs_per_day: Mapped[int] = mapped_column(Integer, default=0)
    agent_run_price_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
