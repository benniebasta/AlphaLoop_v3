"""Backtest run state machine model."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class BacktestRun(Base):
    """
    DB-backed state machine for backtest lifecycle.
    States: pending -> running -> stopping -> paused -> completed
                                    |                    |
                                  failed            killed (timeout)
    """

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # State machine
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    # Process tracking
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Config snapshot
    days: Mapped[int] = mapped_column(Integer, default=365)
    timeframe: Mapped[str] = mapped_column(String(8), default="1h")
    balance: Mapped[float] = mapped_column(Float, default=10000.0)
    max_generations: Mapped[int] = mapped_column(Integer, default=10)
    tools_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Progress
    generation: Mapped[int] = mapped_column(Integer, default=0)
    phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    bars_loaded: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Best result
    best_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    best_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_wr: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_dd: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generations_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Error info
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Checkpoint
    checkpoint_path: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_bt_state", "state"),
        Index("ix_bt_symbol_state", "symbol", "state"),
    )
