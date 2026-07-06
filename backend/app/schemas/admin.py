import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.billing import PaymentPurpose, PaymentStatus, PlanTier, VerificationMethod
from app.models.user import UserRole


class AdminTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    user_id: uuid.UUID
    purpose: PaymentPurpose
    plan_tier: PlanTier | None
    api_id: uuid.UUID | None
    amount_expected_bdt: Decimal
    amount_received_bdt: Decimal | None
    bkash_trx_id: str | None
    status: PaymentStatus
    verification_method: VerificationMethod | None
    note: str | None
    created_at: datetime


class RejectRequest(BaseModel):
    note: str = ""


class AdminSmsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    received_at: datetime
    raw_text: str
    sms_sender: str | None
    parsed_trx_id: str | None
    parsed_amount_bdt: Decimal | None
    parsed_sender_msisdn: str | None
    matched_transaction_id: uuid.UUID | None


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    role: UserRole
    effective_tier: PlanTier


class TierOverrideRequest(BaseModel):
    tier: PlanTier
