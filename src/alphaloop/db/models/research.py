"""Research reports, parameter snapshots, and evolution events."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class ResearchReport(Base):
    """Auto research loop output — one report per session or daily batch."""

    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    report_date: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime)
    period_end: Mapped[datetime | None] = mapped_column(DateTime)
    total_trades: Mapped[int | None] = mapped_column(Integer)
    win_rate: Mapped[float | None] = mapped_column(Float)
    avg_rr: Mapped[float | None] = mapped_column(Float)
    total_pnl_usd: Mapped[float | None] = mapped_column(Float)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    setup_stats: Mapped[dict | None] = mapped_column(JSON)
    session_stats: Mapped[dict | None] = mapped_column(JSON)
    hourly_stats: Mapped[dict | None] = mapped_column(JSON)

    analysis_summary: Mapped[str | None] = mapped_column(Text)
    improvement_suggestions: Mapped[dict | None] = mapped_column(JSON)
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_metrics: Mapped[dict | None] = mapped_column(JSON)


class ParameterSnapshot(Base):
    """Records every parameter change made by the auto-trainer."""

    __tablename__ = "parameter_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapped_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    trigger: Mapped[str | None] = mapped_column(String(64))
    parameters: Mapped[dict | None] = mapped_column(JSON)
    sharpe_at_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate_at_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvolutionEvent(Base):
    """Append-only audit log for all self-evolution events."""

    __tablename__ = "evolution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(16), index=True)
    strategy_version: Mapped[str | None] = mapped_column(String(32), index=True)
    event_type: Mapped[str | None] = mapped_column(String(32), index=True)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics_before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metrics_after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    params_before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    params_after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_evo_symbol_version", "symbol", "strategy_version"),
    )
