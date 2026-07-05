import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.billing import PlanTier
from app.models.user import UserRole


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    picture_url: str | None
    role: UserRole
    settings: dict
    created_at: datetime


class MeOut(UserOut):
    tier: PlanTier
    quota_used_today: int
    quota_limit: int | None


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_saved_logins: bool | None = None
    recorder_channel: Literal["chromium", "chrome"] | None = None
