import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.billing import PaymentPurpose, PaymentStatus, PlanTier, VerificationMethod


class PlanOut(BaseModel):
    tier: PlanTier
    name: str
    price_bdt: int
    daily_creation_limit: int | None
    can_share: bool


class BillingConfigOut(BaseModel):
    receive_msisdn: str


class CreateIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: PaymentPurpose
    plan_tier: PlanTier | None = None
    api_id: uuid.UUID | None = None


class SubmitTrxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trx_id: str = Field(min_length=4, max_length=40)


class PaymentIntentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    purpose: PaymentPurpose
    plan_tier: PlanTier | None
    api_id: uuid.UUID | None
    amount_expected_bdt: Decimal
    amount_received_bdt: Decimal | None
    bkash_trx_id: str | None
    status: PaymentStatus
    verification_method: VerificationMethod | None
    verified_at: datetime | None
    note: str | None
    created_at: datetime
