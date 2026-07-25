import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class MarkParameterIn(BaseModel):
    """Turns a recorded step's literal into a named API parameter — the
    out-of-session equivalent of the recorder's `mark_param` command."""

    model_config = ConfigDict(extra="forbid")

    step_i: int = Field(ge=0)
    # Becomes a JSON body key on the published API, so keep it an identifier.
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=64)
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str | None = None
