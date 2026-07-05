import enum
import uuid

from sqlalchemy import ForeignKey, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column


class WorkflowStatus(str, enum.Enum):
    RECORDING = "recording"
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    start_url: Mapped[str] = mapped_column(Text)
    status: Mapped[WorkflowStatus] = mapped_column(
        enum_column(WorkflowStatus), default=WorkflowStatus.RECORDING)
    steps: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    parameters: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    extraction: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    sample_output: Mapped[dict | None] = mapped_column(JSONB)
    browser_settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    auth_state_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)

    owner: Mapped["User"] = relationship(back_populates="workflows")  # noqa: F821
