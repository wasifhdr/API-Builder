import uuid

from pydantic import BaseModel, field_validator


class CreateRecordingRequest(BaseModel):
    name: str
    start_url: str

    @field_validator("start_url")
    @classmethod
    def validate_start_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("start_url must start with http:// or https://")
        return v


class CreateRecordingResponse(BaseModel):
    workflow_id: uuid.UUID
