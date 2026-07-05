from fastapi import APIRouter

from app.schemas.billing import PlanOut
from app.services.plans import get_plans

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanOut])
async def list_plans() -> list[PlanOut]:
    return [
        PlanOut(
            tier=p.tier,
            name=p.name,
            price_bdt=p.price_bdt,
            daily_creation_limit=p.daily_creation_limit,
            can_share=p.can_share,
        )
        for p in get_plans().values()
    ]
