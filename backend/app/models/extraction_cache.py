import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExtractionSelectorCache(Base):
    """Self-healed selectors, keyed per (workflow, extract ref, field). Written by
    replay when all stored selectors miss and the LLM re-derives a working one.
    Kept out of the workflow snapshot so the authored config stays immutable and
    concurrent replays never race on one JSONB blob."""

    __tablename__ = "extraction_selector_cache"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    field_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    selectors: Mapped[list] = mapped_column(JSONB)
    healed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
