"""
Pydantic v2 models for trade signals.

TradeSignal: Raw signal from the AI signal engine.
ValidatedSignal: Signal after validation (Claude or hard rules).
RejectionFeedback: Structured feedback for rejected signals.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from alphaloop.core.types import SetupType, TrendDirection, ValidationStatus


_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous",
    r"new\s+instructions?",
    r"system\s*:",
    r"<\s*/?\s*system",
    r"you\s+are\s+now",
    r"forget\s+(all\s+)?instructions?",
    r"override\s+(all\s+)?rules?",
]


class TradeSignal(BaseModel):
    """Raw signal from the AI signal engine — not yet validated."""

    trend: TrendDirection
    setup: SetupType
    entry_zone: list[float] = Field(
        min_length=2, max_length=2, description="[price_low, price_high]"
    )
    stop_loss: float = Field(gt=0, description="Stop loss price — must be positive")
    take_profit: list[float] = Field(
        min_length=1, max_length=3, description="[tp1] or [tp1, tp2]"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=20)
    timeframe: str = "M15"
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("reasoning")
    @classmethod
    def sanitize_reasoning(cls, v: str) -> str:
        clean = re.sub(r"[\x00-\x1f\x7f]", " ", v)
        lower = clean.lower()
        for pat in _INJECTION_PATTERNS:
            if re.search(pat, lower):
                raise ValueError(
                    f"Reasoning contains suspected prompt injection pattern: {pat}"
                )
        return clean[:500].strip()

    @field_validator("take_profit")
    @classmethod
    def take_profit_valid(cls, v: list[float]) -> list[float]:
        for i, tp in enumerate(v):
            if tp is None or not isinstance(tp, (int, float)) or tp <= 0:
                raise ValueError(
                    f"take_profit[{i}] must be a positive float, got {tp!r}"
                )
        return [float(tp) for tp in v]

    @field_validator("entry_zone")
    @classmethod
    def entry_zone_valid(cls, v: list[float]) -> list[float]:
        if v[0] <= 0 or v[1] <= 0:
            raise ValueError(f"entry_zone values must be positive, got {v}")
        if v[0] >= v[1]:
            raise ValueError("entry_zone[0] must be < entry_zone[1]")
        return v

    @model_validator(mode="after")
    def sl_not_in_entry_zone(self) -> TradeSignal:
        low, high = self.entry_zone
        if low <= self.stop_loss <= high:
            raise ValueError("SL cannot be inside entry zone")
        return self

    @model_validator(mode="after")
    def tp_direction_matches_trade(self) -> TradeSignal:
        direction = self.direction
        if direction == "BUY":
            for i, tp in enumerate(self.take_profit):
                if tp <= self.entry_zone[1]:
                    raise ValueError(
                        f"BUY take_profit[{i}]={tp} must be above "
                        f"entry_zone top {self.entry_zone[1]}"
                    )
        elif direction == "SELL":
            for i, tp in enumerate(self.take_profit):
                if tp >= self.entry_zone[0]:
                    raise ValueError(
                        f"SELL take_profit[{i}]={tp} must be below "
                        f"entry_zone bottom {self.entry_zone[0]}"
                    )
        return self

    @property
    def entry_mid(self) -> float:
        return (self.entry_zone[0] + self.entry_zone[1]) / 2

    @property
    def direction(self) -> str:
        if self.trend == TrendDirection.BULLISH:
            return "BUY"
        elif self.trend == TrendDirection.BEARISH:
            return "SELL"
        return "NEUTRAL"

    @property
    def rr_ratio_tp1(self) -> Optional[float]:
        if not self.take_profit:
            return None
        entry = self.entry_mid
        risk = abs(entry - self.stop_loss)
        reward = abs(self.take_profit[0] - entry)
        return round(reward / risk, 2) if risk > 0 else None


class RejectionFeedback(BaseModel):
    """Structured rejection feedback passed back to signal engine."""

    reason_code: str = ""
    parameter_violated: str = ""
    suggested_adjustment: str = ""
    severity: str = "medium"


class ValidatedSignal(BaseModel):
    """Signal after validation — contains adjusted levels and risk score."""

    original: TradeSignal
    status: ValidationStatus
    rejection_reasons: list[str] = []
    rejection_feedback: list[RejectionFeedback] = []
    adjusted_entry: Optional[float] = None
    adjusted_sl: Optional[float] = None
    adjusted_tp: Optional[list[float]] = None
    risk_score: float = Field(ge=0.0, le=1.0, default=0.5)
    validator_reasoning: str = ""
    validated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def final_entry(self) -> float:
        return self.adjusted_entry or self.original.entry_mid

    @property
    def final_sl(self) -> float:
        return self.adjusted_sl or self.original.stop_loss

    @property
    def final_tp(self) -> list[float]:
        return self.adjusted_tp if self.adjusted_tp is not None else self.original.take_profit
