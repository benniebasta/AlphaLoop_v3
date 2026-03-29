"""Pipeline decision and rejection log models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class PipelineDecision(Base):
    """Append-only audit trail of every FilterPipeline decision."""

    __tablename__ = "pipeline_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blocked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_modifier: Mapped[float | None] = mapped_column(Float, nullable=True)
    bias: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tool_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (
        Index("ix_pipeline_symbol_at", "symbol", "occurred_at"),
        Index("ix_pipeline_allowed", "allowed"),
    )


class RejectionLog(Base):
    """Tracks rejected signals for pattern detection."""

    __tablename__ = "rejection_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(16), index=True)
    direction: Mapped[str | None] = mapped_column(String(8))
    setup_type: Mapped[str | None] = mapped_column(String(32))
    session_name: Mapped[str | None] = mapped_column(String(32))
    rejected_by: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_rejection_pattern", "symbol", "setup_type", "session_name", "direction"),
    )
