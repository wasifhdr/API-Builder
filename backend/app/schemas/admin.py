import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.api import ApiVisibility, SpecStatus
from app.models.billing import PaymentPurpose, PaymentStatus, PlanTier, SubscriptionStatus, VerificationMethod
from app.models.user import UserRole
from app.models.workflow import WorkflowStatus


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
    username: str | None = None
    phone: str | None = None
    suspended_at: datetime | None = None
    workflow_count: int = 0
    api_count: int = 0
    key_count: int = 0


class AdminSubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tier: PlanTier
    status: SubscriptionStatus
    expires_at: datetime


class AdminUserDetailOut(AdminUserOut):
    created_at: datetime
    has_password: bool
    has_google: bool
    subscription: AdminSubscriptionOut | None = None


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: PlanTier | None = None
    name: str | None = None
    phone: str | None = None
    role: UserRole | None = None
    suspended: bool | None = None


class AdminKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    label: str
    key_prefix: str
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AdminKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str


class AdminAuditLogOut(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    actor_username: str | None
    action: str
    target_type: str
    target_id: str
    detail: dict
    created_at: datetime


class AdminPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    tier: PlanTier
    price_bdt: int
    daily_creation_limit: int | None
    can_share: bool
    updated_at: datetime


class AdminPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price_bdt: int | None = Field(default=None, ge=0)
    daily_creation_limit: int | None = None
    can_share: bool | None = None


class AdminApiOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    owner_id: uuid.UUID
    owner_email: str
    owner_username: str | None
    slug: str
    name: str
    visibility: ApiVisibility
    is_active: bool
    spec_status: SpecStatus
    execution_count: int
    created_at: datetime


class AdminApiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


class AdminWorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    status: WorkflowStatus
    created_at: datetime


class AdminStatsDayOut(BaseModel):
    date: str
    total: int
    succeeded: int


class AdminStatsOut(BaseModel):
    total_users: int
    new_users_7d: int
    suspended_users: int
    total_apis: int
    active_apis: int
    executions_by_day: list[AdminStatsDayOut]
    success_rate_7d: float
    revenue_verified_bdt: Decimal
    pending_payments: int
