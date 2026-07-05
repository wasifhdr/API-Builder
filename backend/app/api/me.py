from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import current_user
from app.db import get_db
from app.models.user import User
from app.schemas.user import SettingsUpdate, UserOut

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=UserOut)
async def get_me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/settings", response_model=UserOut)
async def update_settings(
    body: SettingsUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    merged = {**user.settings, **body.model_dump(exclude_unset=True)}
    user.settings = merged
    await db.commit()
    await db.refresh(user)
    return user
