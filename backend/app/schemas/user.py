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
    replay_headless: bool | None = None


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


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    phone: str | None = None


class PasswordSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str | None = None
    new_password: str


class SessionOut(BaseModel):
    sid_prefix: str
    created_at: datetime
    user_agent: str | None
    ip: str | None
    current: bool


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_username: str
    current_password: str | None = None
