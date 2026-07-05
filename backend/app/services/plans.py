from dataclasses import dataclass

from app.config import settings
from app.models.billing import PlanTier


@dataclass(frozen=True)
class PlanConfig:
    tier: PlanTier
    name: str
    price_bdt: int
    daily_creation_limit: int | None  # None = unlimited
    can_share: bool


def get_plans() -> dict[PlanTier, PlanConfig]:
    return {
        PlanTier.FREE: PlanConfig(PlanTier.FREE, "Free", 0, 5, False),
        PlanTier.PRO: PlanConfig(PlanTier.PRO, "Pro", settings.plan_price_pro_bdt, 50, True),
        PlanTier.MAX: PlanConfig(PlanTier.MAX, "Max", settings.plan_price_max_bdt, None, True),
    }


def plan_for(tier: PlanTier) -> PlanConfig:
    return get_plans()[tier]
