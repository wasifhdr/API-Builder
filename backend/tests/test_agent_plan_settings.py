from decimal import Decimal

import pytest

from app.models.billing import PlanTier
from app.services import plans
from app.services.plans import invalidate_cache


@pytest.fixture(autouse=True)
def _reset_plan_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.mark.asyncio
async def test_free_tier_gets_no_agent_runs(db):
    config = await plans.plan_for(PlanTier.FREE, db)
    assert config.agent_runs_per_day == 0


@pytest.mark.asyncio
async def test_pro_and_max_get_agent_runs(db):
    pro = await plans.plan_for(PlanTier.PRO, db)
    max_ = await plans.plan_for(PlanTier.MAX, db)
    assert pro.agent_runs_per_day > 0
    assert max_.agent_runs_per_day > 0


@pytest.mark.asyncio
async def test_agent_run_price_is_positive_money(db):
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert isinstance(price, Decimal)
    assert price > 0


@pytest.mark.asyncio
async def test_agent_runs_per_day_accessor_matches_plan_config(db):
    via_accessor = await plans.agent_runs_per_day(PlanTier.MAX, db)
    via_config = (await plans.plan_for(PlanTier.MAX, db)).agent_runs_per_day
    assert via_accessor == via_config
