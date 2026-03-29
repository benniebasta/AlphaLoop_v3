"""Strategy version persistence model."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class StrategyVersion(Base):
    """
    DB-backed strategy version record.

    Mirrors the strategy_versions/*.json files but provides
    queryable persistence for the deployment pipeline.

    Lifecycle: candidate → dry_run → demo → live → retired
    """

    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    seed_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Strategy parameters
    params_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tools_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ai_models_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Performance summary
    total_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Monte Carlo validation
    mc_p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    mc_significant: Mapped[bool | None] = mapped_column(nullable=True)
    mc_ruin_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Canary deployment tracking
    canary_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canary_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    canary_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    canary_result: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Deployment timestamps
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # File reference
    file_path: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_strat_symbol_version", "symbol", "version", unique=True),
        Index("ix_strat_status", "status"),
        Index("ix_strat_symbol_status", "symbol", "status"),
    )
