import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentRunCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=500)


class ConfirmUrlIn(BaseModel):
    ok: bool
    # The planner picks the start URL from model knowledge alone, with no
    # search and no verification, so it is sometimes wrong. This is the
    # user's correction channel.
    url: str | None = None


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    prompt: str
    resolved_url: str | None
    plan: dict
    attempt: int
    failure_reason: str | None
    workflow_id: uuid.UUID | None
    created_at: datetime
