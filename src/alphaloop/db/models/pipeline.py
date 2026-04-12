"""Pipeline decision, archival, and rejection log models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class PipelineDecision(Base):
    """Append-only audit trail of every v4 pipeline decision."""

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


class PipelineDecisionArchive(Base):
    """Cold-storage archive for aged pipeline decisions and candidate journeys."""

    __tablename__ = "pipeline_decision_archive"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_decision_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    archived_at: Mapped[datetime] = mapped_column(
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
        Index("ix_pipeline_archive_symbol_at", "symbol", "occurred_at"),
        Index("ix_pipeline_archive_allowed", "allowed"),
    )


class PipelineStageDecision(Base):
    """Per-stage pipeline ledger row — one per (cycle, stage).

    Powers the Gate-1 observability funnel.  Written alongside the existing
    cycle-level PipelineDecision table but stored in a separate table so the
    funnel endpoint can query pass/reject counts per stage without unpacking
    the legacy ``tool_results.journey`` JSON blob.
    """

    __tablename__ = "pipeline_stage_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(16), default="live", index=True)  # live | backtest_replay
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    stage_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    blocked_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Cycle-level context duplicated on each row so the funnel endpoint can
    # group without a join.  Cheap on SQLite, negligible on Postgres.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    reject_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    setup_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conviction_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_psd_symbol_at", "symbol", "occurred_at"),
        Index("ix_psd_stage_status", "stage", "status"),
        Index("ix_psd_cycle", "cycle_id"),
        Index("ix_psd_source_at", "source", "occurred_at"),
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
