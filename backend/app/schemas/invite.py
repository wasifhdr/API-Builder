import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.api import GrantSource


class CreateInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_uses: int | None = None
    expires_at: datetime | None = None


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
    valid: bool
    reason: str | None = None


class AcceptInviteResult(BaseModel):
    status: str
    payment_intent_id: uuid.UUID | None = None
    amount_expected_bdt: str | None = None
