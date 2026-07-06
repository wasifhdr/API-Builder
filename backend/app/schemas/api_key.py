import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    key_prefix: str
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyOut):
    api_key: str  # plaintext — shown exactly once, at creation


class ApiKeyCreateRequest(BaseModel):
    label: str = "default"
