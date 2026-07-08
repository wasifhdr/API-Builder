import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.api import ApiPricingMode, GrantSource

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CreateInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_uses: int | None = None
    expires_at: datetime | None = None


class AddAllowedEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.match(normalized):
            raise ValueError("invalid email address")
        return normalized


class AllowedEmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    api_id: uuid.UUID
    email: str
    created_at: datetime


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    api_id: uuid.UUID
    token: str
    max_uses: int | None
    use_count: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    api_id: uuid.UUID
    user_id: uuid.UUID
    granted_via: GrantSource
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitePreviewOut(BaseModel):
    api_name: str
    api_slug: str
    price_bdt: str | None
    pricing_mode: ApiPricingMode
    valid: bool
    reason: str | None = None


class AcceptInviteResult(BaseModel):
    status: str  # "granted" | "insufficient_balance"
    price_bdt: str | None = None
    balance_bdt: str | None = None
