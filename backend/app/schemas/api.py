import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.api import ApiPricingMode, ApiVisibility, SpecStatus
from app.models.execution import ExecutionStatus


class CustomApiOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID
    owner_id: uuid.UUID
    slug: str
    name: str
    description: str | None
    visibility: ApiVisibility
    price_bdt: Decimal | None
    pricing_mode: ApiPricingMode
    included_call_quota: int | None
    spec_status: SpecStatus
    openapi_spec: dict | None
    cache_ttl_seconds: int
    is_active: bool
    created_at: datetime


class CustomApiUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_ttl_seconds: int | None = None
    is_active: bool | None = None
    visibility: ApiVisibility | None = None
    price_bdt: Decimal | None = None
    pricing_mode: ApiPricingMode | None = None
    included_call_quota: int | None = None


class ApiExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ExecutionStatus
    params: dict
    result: Any | None
    error_message: str | None
    failure_artifact_path: str | None
    cache_hit: bool
    created_at: datetime
    duration_ms: int | None


class ApiStatsDayOut(BaseModel):
    date: str
    total: int
    succeeded: int


class ApiStatsConsumerOut(BaseModel):
    name: str
    calls_30d: int


class ApiStatsOut(BaseModel):
    total_calls: int
    calls_7d: int
    success_rate_7d: float
    avg_duration_ms_7d: float | None
    cache_hit_rate_7d: float
    calls_by_day: list[ApiStatsDayOut]
    top_consumers: list[ApiStatsConsumerOut]
    last_called_at: datetime | None
