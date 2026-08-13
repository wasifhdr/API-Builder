import enum
import uuid

from sqlalchemy import ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_column


class AgentRunStatus(str, enum.Enum):
    PLANNING = "planning"
    AWAITING_CONFIRM = "awaiting_confirm"
    DRIVING = "driving"
    DISTILLING = "distilling"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATUSES = {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Nullable: a run that fails before distilling never produces a workflow.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), index=True)

    prompt: Mapped[str] = mapped_column(Text)
    resolved_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus), default=AgentRunStatus.PLANNING, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    # Declared parameters (with drive/verify values) and output fields, set by
    # the plan phase before the browser opens. JSONB: replace, never mutate.
    plan: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    transcript: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))

    failure_reason: Mapped[str | None] = mapped_column(Text)
    token_usage: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
