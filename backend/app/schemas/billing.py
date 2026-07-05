from pydantic import BaseModel

from app.models.billing import PlanTier


class PlanOut(BaseModel):
    tier: PlanTier
    name: str
    price_bdt: int
    daily_creation_limit: int | None
    can_share: bool
