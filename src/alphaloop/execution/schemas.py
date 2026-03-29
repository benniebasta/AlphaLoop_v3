"""Pydantic schemas for execution layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class OrderResult(BaseModel):
    """Result of a trade execution attempt."""

    success: bool
    order_ticket: Optional[int] = None
    fill_price: Optional[float] = None
    fill_volume: Optional[float] = None
    spread_at_fill: Optional[float] = None
    slippage_points: Optional[float] = None
    error_code: Optional[int] = None
    error_message: str = ""
    executed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Position(BaseModel):
    """An open trading position."""

    ticket: int
    symbol: str
    direction: str  # BUY | SELL
    volume: float
    entry_price: float
    current_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    profit_usd: float = 0.0
    swap: float = 0.0
    magic: int = 0
    opened_at: Optional[datetime] = None


class SizingResult(BaseModel):
    """Output of the position sizer."""

    lot_size: float
    risk_amount_usd: float
    margin_required: float
    margin_percent: float
    adjusted_risk_pct: float
    modifiers_applied: list[str] = []
