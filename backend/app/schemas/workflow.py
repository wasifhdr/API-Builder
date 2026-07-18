import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.workflow import WorkflowStatus


class WorkflowListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    start_url: str
    status: WorkflowStatus
    created_at: datetime
    updated_at: datetime


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    start_url: str
    status: WorkflowStatus
    steps: list
    parameters: list
    extraction: dict
    output_schema: dict | None
    sample_output: Any | None
    created_at: datetime
    updated_at: datetime
    published_api_id: uuid.UUID | None = None
    published_api_slug: str | None = None


class WorkflowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    parameters: list[dict] | None = None
    extraction: dict | None = None
