import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.billing import PaymentPurpose, PaymentStatus, PlanTier, VerificationMethod
from app.models.wallet import CashoutStatus


class PlanOut(BaseModel):
    tier: PlanTier
    name: str
    price_bdt: int
    daily_creation_limit: int | None
    can_share: bool
    monthly_call_quota: int | None
    platform_cut_pct: Decimal
    can_cashout: bool
    max_invitees_per_api: int | None


class BillingConfigOut(BaseModel):
    receive_msisdn: str


class CreateIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: PaymentPurpose
    amount_bdt: Decimal | None = None


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_tier: PlanTier


class SubscribeResult(BaseModel):
    tier: PlanTier
    expires_at: datetime
    balance_bdt: Decimal


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


class WalletOut(BaseModel):
    balance_bdt: Decimal
    earnings_bdt: Decimal
    can_cashout: bool
    platform_cut_pct: Decimal


class WalletLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    bucket: str
    amount_bdt: Decimal
    reason: str
    balance_after_bdt: Decimal
    execution_id: uuid.UUID | None
    api_id: uuid.UUID | None
    transaction_id: uuid.UUID | None
    counterparty_user_id: uuid.UUID | None
    created_at: datetime


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_bdt: Decimal | None = None


class SweepResult(BaseModel):
    swept_bdt: Decimal
    balance_bdt: Decimal
    earnings_bdt: Decimal


class CashoutRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_bdt: Decimal = Field(gt=0)
    payout_msisdn: str = Field(min_length=6, max_length=20)


class CashoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    amount_bdt: Decimal
    payout_msisdn: str
    status: CashoutStatus
    bkash_trx_id: str | None
    note: str | None
    created_at: datetime
    decided_at: datetime | None
