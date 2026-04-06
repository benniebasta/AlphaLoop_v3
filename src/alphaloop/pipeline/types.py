"""
pipeline/types.py — Interface dataclasses for the 8-stage institutional pipeline.

Each stage produces a typed result consumed by downstream stages.
All timestamps are UTC. Scores are 0-100. Scalars are 0.0-1.25.
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Universal outcome convention
# ---------------------------------------------------------------------------

class CycleOutcome(str, Enum):
    """Every cycle resolves to exactly one of these."""

    NO_SIGNAL = "no_signal"             # Signal engine found nothing
    NO_CONSTRUCTION = "no_construction"  # Direction hypothesis exists but no valid trade constructed
    REJECTED = "rejected"                # Invalid or unsafe
    HELD = "held"                        # Insufficient edge
    DELAYED = "delayed"                  # Transient execution issue, queued for retry
    TRADE_OPENED = "trade_opened"        # Successfully executed
    ORDER_FAILED = "order_failed"        # Broker / margin error


# ---------------------------------------------------------------------------
# Stage 1: MarketGate
# ---------------------------------------------------------------------------

@dataclass
class MarketGateResult:
    """Binary: is the market physically tradeable right now?"""

    tradeable: bool
    block_reason: str | None = None
    blocked_by: str | None = None
    data_quality: float = 1.0
    spread_ratio: float = 1.0
    bars_available: int = 0
    timestamp: datetime = field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Stage 2: RegimeClassifier
# ---------------------------------------------------------------------------

@dataclass
class PortfolioContext:
    """Early portfolio exposure snapshot (computed alongside regime)."""

    same_symbol_exposure: int = 0
    same_direction_exposure: int = 0
    correlated_exposure: float = 0.0
    macro_exposure: str | None = None
    portfolio_heat_pct: float = 0.0
    risk_budget_remaining_pct: float = 1.0


@dataclass
class RegimeSnapshot:
    """Market state classification.  NEVER blocks."""

    regime: str  # trending | ranging | volatile | neutral
    macro_regime: str  # risk_on | risk_off | neutral
    volatility_band: str  # compressed | normal | elevated | extreme
    allowed_setups: list[str] = field(default_factory=list)
    atr_pct: float = 0.0
    choppiness: float = 50.0
    adx: float = 25.0
    session_quality: float = 0.5
    confidence_ceiling: float = 90.0
    min_entry_adjustment: float = 0.0
    size_multiplier: float = 1.0
    weight_overrides: dict[str, float] = field(default_factory=dict)
    portfolio_context: PortfolioContext | None = None
    timestamp: datetime = field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Stage 3: Direction Hypothesis  (constraint-first refactor)
# ---------------------------------------------------------------------------

@dataclass
class DirectionHypothesis:
    """Direction-only output from a signal engine.

    Contains NO SL/TP — price levels are derived downstream by
    :class:`TradeConstructor` (Stage 3B) from market structure.
    """

    direction: str          # BUY | SELL
    confidence: float       # 0.0-1.0
    setup_tag: str          # pullback | breakout | reversal | …
    reasoning: str = ""
    source_names: str = ""  # e.g. "ema_crossover+macd_crossover (AND)"
    generated_at: datetime = field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Stage 3 / 3B: SignalGenerator → TradeConstructor
# ---------------------------------------------------------------------------

@dataclass
class CandidateSignal:
    """Proposed trade — fully constructed with structure-derived SL/TP."""

    direction: str  # BUY | SELL
    setup_type: str  # pullback | breakout | reversal | continuation | range_bounce
    entry_zone: tuple[float, float]  # (low, high)
    stop_loss: float
    take_profit: list[float]  # [tp1, tp2]
    raw_confidence: float  # 0.0-1.0
    rr_ratio: float = 0.0
    signal_sources: list[str] = field(default_factory=list)
    reasoning: str = ""
    regime_at_generation: str = "neutral"
    generated_at: datetime = field(default_factory=_utcnow)
    # ── Construction provenance (Phase 1 constraint-first refactor) ──
    sl_source: str = ""               # e.g. "swing_low", "fvg_bottom"
    construction_candidates: int = 0  # how many SL candidates were evaluated


# ---------------------------------------------------------------------------
# Stage 4A: StructuralInvalidation
# ---------------------------------------------------------------------------

@dataclass
class InvalidationFailure:
    """A single invalidation check outcome."""

    check_name: str
    severity: str  # HARD_INVALIDATE | SOFT_INVALIDATE
    reason: str
    measured_value: float | None = None
    threshold: float | None = None


@dataclass
class InvalidationResult:
    """Aggregated invalidation outcome for a candidate signal."""

    severity: str  # HARD_INVALIDATE | SOFT_INVALIDATE | PASS
    failures: list[InvalidationFailure] = field(default_factory=list)
    conviction_penalty: float = 0.0  # 0 for PASS, negative for SOFT
    checks_run: list[str] = field(default_factory=list)
    setup_type: str = ""


# ---------------------------------------------------------------------------
# Stage 4B: StructuralQuality
# ---------------------------------------------------------------------------

@dataclass
class QualityResult:
    """Soft structural scores.  NEVER blocks."""

    tool_scores: dict[str, float] = field(default_factory=dict)
    group_scores: dict[str, float] = field(default_factory=dict)
    overall_score: float = 50.0
    contradictions: list[str] = field(default_factory=list)
    low_score_count: int = 0
    max_score: float = 50.0


# ---------------------------------------------------------------------------
# Stage 5: ConvictionScorer
# ---------------------------------------------------------------------------

@dataclass
class ConvictionScore:
    """Weighted conviction with quality floors and penalty accounting."""

    score: float = 0.0  # 0-100
    normalized: float = 0.0  # 0.0-1.0
    size_scalar: float = 0.0  # 0.0 (HOLD) | 0.6 (min) | 1.0 (full)
    decision: str = "HOLD"  # TRADE | HOLD
    hold_reason: str | None = None
    regime_min_entry: float = 55.0
    regime_ceiling: float = 90.0
    group_contributions: dict[str, float] = field(default_factory=dict)
    conflict_penalty: float = 0.0
    invalidation_penalty: float = 0.0
    portfolio_penalty: float = 0.0
    total_penalty: float = 0.0  # sum of all penalties (capped by budget)
    penalty_budget_used: float = 0.0
    penalty_budget_cap: float = 50.0  # MAX_TOTAL_CONVICTION_PENALTY
    penalties_prorated: bool = False  # True if budget cap forced pro-rating
    setup_calibration: float = 1.0
    quality_floor_triggered: bool = False
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Stage 7: RiskGate
# ---------------------------------------------------------------------------

@dataclass
class RiskGateResult:
    """Risk capacity check.  Can block or reduce size."""

    allowed: bool = True
    block_reason: str | None = None
    size_modifier: float = 1.0
    equity_curve_scalar: float = 1.0
    risk_utilization: float = 0.0


# ---------------------------------------------------------------------------
# Stage 8: ExecutionGuard
# ---------------------------------------------------------------------------

@dataclass
class ExecutionGuardResult:
    """Execution safety.  Can execute, delay, or block."""

    action: str = "EXECUTE"  # EXECUTE | DELAY | BLOCK
    delay_candles: int = 0
    delay_reason: str | None = None
    block_reason: str | None = None
    blocked_by: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------

@dataclass
class SizingDecision:
    """Final position sizing after all scalars applied."""

    lots: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    sl_distance_pts: float = 0.0
    conviction_scalar: float = 1.0
    regime_scalar: float = 1.0
    freshness_scalar: float = 1.0
    risk_gate_scalar: float = 1.0
    equity_curve_scalar: float = 1.0
    final_risk_pct: float = 0.0
    margin_required: float = 0.0


# ---------------------------------------------------------------------------
# Candidate Journey
# ---------------------------------------------------------------------------


@dataclass
class CandidateJourneyStage:
    """One durable stage entry in the candidate decision trail."""

    stage: str
    status: str
    detail: str = ""
    blocked_by: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "blocked_by": self.blocked_by,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class CandidateJourney:
    """Stage-by-stage decision trail for one pipeline candidate."""

    stages: list[CandidateJourneyStage] = field(default_factory=list)
    final_outcome: str | None = None
    rejection_reason: str | None = None

    def add_stage(
        self,
        stage: str,
        status: str,
        *,
        detail: str = "",
        blocked_by: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.stages.append(
            CandidateJourneyStage(
                stage=stage,
                status=status,
                detail=detail,
                blocked_by=blocked_by,
                payload=payload or {},
            )
        )

    def finalize(self, *, outcome: str, rejection_reason: str | None = None) -> None:
        self.final_outcome = outcome
        self.rejection_reason = rejection_reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [stage.to_dict() for stage in self.stages],
            "final_outcome": self.final_outcome,
            "rejection_reason": self.rejection_reason,
        }
