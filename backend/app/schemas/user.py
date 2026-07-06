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
    username: str | None
    name: str | None
    phone: str | None
    picture_url: str | None
    role: UserRole
    has_password: bool
    has_google: bool
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


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str
    username: str
    password: str


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class ClaimUsernameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str


class UsernameAvailableOut(BaseModel):
    available: bool
