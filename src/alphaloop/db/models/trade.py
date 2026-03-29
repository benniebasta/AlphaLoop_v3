"""Trade log and audit trail models."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class TradeLog(Base):
    """Core trade record — one row per trade execution."""

    __tablename__ = "trade_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Signal metadata
    signal_id: Mapped[str | None] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), default="XAUUSD")
    direction: Mapped[str | None] = mapped_column(String(8))
    setup_type: Mapped[str | None] = mapped_column(String(32))
    timeframe: Mapped[str | None] = mapped_column(String(8))

    # Entry details
    entry_price: Mapped[float | None] = mapped_column(Float)
    entry_zone_low: Mapped[float | None] = mapped_column(Float)
    entry_zone_high: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit_1: Mapped[float | None] = mapped_column(Float)
    take_profit_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    lot_size: Mapped[float | None] = mapped_column(Float)
    risk_pct: Mapped[float | None] = mapped_column(Float)
    risk_amount_usd: Mapped[float | None] = mapped_column(Float)

    # Execution timestamps
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    session_name: Mapped[str | None] = mapped_column(String(32))

    # Outcome
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hit_tp1: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_tp2: Mapped[bool] = mapped_column(Boolean, default=False)
    hit_sl: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_manually: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)

    # Signal quality
    qwen_confidence: Mapped[float | None] = mapped_column(Float)
    claude_risk_score: Mapped[float | None] = mapped_column(Float)
    rr_ratio: Mapped[float | None] = mapped_column(Float)
    macro_bias: Mapped[str | None] = mapped_column(String(16))
    macro_modifier: Mapped[float | None] = mapped_column(Float)

    # Market conditions at entry
    h1_rsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    h1_atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    h1_trend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    m15_structure: Mapped[str | None] = mapped_column(String(16), nullable=True)
    liquidity_sweep_detected: Mapped[bool] = mapped_column(Boolean, default=False)

    # Raw data blobs
    signal_json: Mapped[dict | None] = mapped_column(JSON)
    validation_json: Mapped[dict | None] = mapped_column(JSON)
    market_context_snapshot: Mapped[dict | None] = mapped_column(JSON)

    # Strategy version
    strategy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Instance tracking
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_dry_run: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)

    # Leverage & margin
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    margin_required: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Repositioning events
    reposition_log: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Execution quality
    execution_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_points: Mapped[float | None] = mapped_column(Float, nullable=True)

    # P&L attribution
    pnl_entry_skill: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_exit_skill: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_slippage_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_commission_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Notes
    claude_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_trade_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_trade_opened_at", "opened_at"),
        Index("ix_trade_outcome", "outcome"),
        Index("ix_trade_setup_session", "setup_type", "session_name"),
        Index("ix_trade_instance_outcome", "instance_id", "outcome"),
        Index("ix_research_query", "symbol", "strategy_version", "outcome"),
    )


class TradeAuditLog(Base):
    """Immutable audit trail for trade record mutations."""

    __tablename__ = "trade_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    changed_by: Mapped[str] = mapped_column(String(64), default="system")
