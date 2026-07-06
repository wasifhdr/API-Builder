import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.billing import PlanTier
from app.models.plan_settings import PlanSettings

CACHE_TTL_SECONDS = 30


@dataclass(frozen=True)
class PlanConfig:
    tier: PlanTier
    name: str
    price_bdt: int
    daily_creation_limit: int | None  # None = unlimited
    can_share: bool


def _defaults() -> dict[PlanTier, PlanConfig]:
    # Fallback for any tier missing from the plan_settings table — e.g. the test
    # database (created via Base.metadata.create_all, never seeded by migrations)
    # or a fresh dev DB before the seed migration has run.
    return {
        PlanTier.FREE: PlanConfig(PlanTier.FREE, "Free", 0, 5, False),
        PlanTier.PRO: PlanConfig(PlanTier.PRO, "Pro", settings.plan_price_pro_bdt, 50, True),
        PlanTier.MAX: PlanConfig(PlanTier.MAX, "Max", settings.plan_price_max_bdt, None, True),
    }


_NAMES: dict[PlanTier, str] = {PlanTier.FREE: "Free", PlanTier.PRO: "Pro", PlanTier.MAX: "Max"}

_cache: dict[PlanTier, PlanConfig] | None = None
_cache_at: float = 0.0


def invalidate_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def get_plans(db: AsyncSession) -> dict[PlanTier, PlanConfig]:
    global _cache, _cache_at

    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < CACHE_TTL_SECONDS:
        return _cache

    defaults = _defaults()
    result = await db.execute(select(PlanSettings))
    rows = {PlanTier(row.tier): row for row in result.scalars().all()}

    plans: dict[PlanTier, PlanConfig] = {}
    for tier in PlanTier:
        row = rows.get(tier)
        if row is None:
            plans[tier] = defaults[tier]
        else:
            plans[tier] = PlanConfig(
                tier=tier,
                name=_NAMES[tier],
                price_bdt=row.price_bdt,
                daily_creation_limit=row.daily_creation_limit,
                can_share=row.can_share,
            )

    _cache = plans
    _cache_at = now
    return plans


async def plan_for(tier: PlanTier, db: AsyncSession) -> PlanConfig:
    plans = await get_plans(db)
    return plans[tier]
