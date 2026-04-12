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
    source_detail: dict = field(default_factory=dict)  # attribution: mode, rules, regime, etc.
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


# ---------------------------------------------------------------------------
# TradeDecision — unified per-cycle explainability object (Gate-1 observability)
# ---------------------------------------------------------------------------

@dataclass
class TradeDecision:
    """Single per-cycle explainability record used by UI + funnel endpoint.

    Read-only projection of PipelineResult — never drives behaviour, only
    displayed in the observability UI so every blocked trade is traceable
    to a stage, a reason, and the exact penalty/scalar chain.
    """

    symbol: str = ""
    mode: str = "algo_only"
    direction: str | None = None
    setup_type: str | None = None
    outcome: str = ""  # CycleOutcome.value
    reject_stage: str | None = None
    reject_reason: str | None = None
    confidence_raw: float | None = None
    confidence_adjusted: float | None = None
    conviction_score: float | None = None
    conviction_decision: str | None = None
    penalties: list[dict[str, Any]] = field(default_factory=list)  # [{source, points, reason}]
    size_multiplier: float = 1.0  # product of all size scalars (0 if not executed)
    hard_block: bool = False
    ai_verdict: str = "skipped"  # approve | reduce | reject | skipped
    execution_status: str = "none"  # executed | delayed | blocked | none
    latency_ms: float = 0.0
    journey: CandidateJourney | None = None
    occurred_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mode": self.mode,
            "direction": self.direction,
            "setup_type": self.setup_type,
            "outcome": self.outcome,
            "reject_stage": self.reject_stage,
            "reject_reason": self.reject_reason,
            "confidence_raw": self.confidence_raw,
            "confidence_adjusted": self.confidence_adjusted,
            "conviction_score": self.conviction_score,
            "conviction_decision": self.conviction_decision,
            "penalties": list(self.penalties),
            "size_multiplier": self.size_multiplier,
            "hard_block": self.hard_block,
            "ai_verdict": self.ai_verdict,
            "execution_status": self.execution_status,
            "latency_ms": self.latency_ms,
            "journey": self.journey.to_dict() if self.journey else None,
            "occurred_at": self.occurred_at.isoformat(),
        }


def build_trade_decision(result: Any, *, symbol: str, mode: str) -> TradeDecision:
    """Project a PipelineResult into a TradeDecision (read-only, no side effects).

    Lives in types.py to avoid circular imports with orchestrator — the
    orchestrator and trading loop both call this after _finalise().
    """
    outcome_value = getattr(getattr(result, "outcome", None), "value", str(getattr(result, "outcome", "")))
    rejection_reason = getattr(result, "rejection_reason", None)
    journey = getattr(result, "journey", None)

    # Resolve the reject stage from the last journey entry that has a blocked_by marker.
    reject_stage: str | None = None
    if journey and journey.stages:
        for stage in reversed(journey.stages):
            if stage.blocked_by:
                reject_stage = stage.blocked_by
                break
        if reject_stage is None:
            # NO_SIGNAL / NO_CONSTRUCTION have no blocked_by but still resolve to a stage name
            last = journey.stages[-1]
            if last.status in ("no_signal", "rejected", "held", "soft_invalidated"):
                reject_stage = last.stage

    signal = getattr(result, "signal", None)
    hypothesis = getattr(result, "hypothesis", None)
    conviction = getattr(result, "conviction", None)
    sizing = getattr(result, "sizing", None)
    risk_gate = getattr(result, "risk_gate", None)
    invalidation = getattr(result, "invalidation", None)
    execution_guard = getattr(result, "execution_guard", None)

    direction = None
    setup_type = None
    raw_conf = None
    if signal is not None:
        direction = getattr(signal, "direction", None)
        setup_type = getattr(signal, "setup_type", None)
        raw_conf = getattr(signal, "raw_confidence", None)
    elif hypothesis is not None:
        direction = getattr(hypothesis, "direction", None)
        setup_type = getattr(hypothesis, "setup_tag", None)
        raw_conf = getattr(hypothesis, "confidence", None)

    penalties: list[dict[str, Any]] = []
    conviction_score: float | None = None
    conviction_decision: str | None = None
    adjusted_conf: float | None = None
    if conviction is not None:
        conviction_score = float(getattr(conviction, "score", 0.0))
        conviction_decision = getattr(conviction, "decision", None)
        adjusted_conf = float(getattr(conviction, "normalized", 0.0)) or None
        inv_pen = float(getattr(conviction, "invalidation_penalty", 0.0) or 0.0)
        if inv_pen:
            penalties.append({"source": "invalidation", "points": inv_pen, "reason": "soft invalidation"})
        conf_pen = float(getattr(conviction, "conflict_penalty", 0.0) or 0.0)
        if conf_pen:
            penalties.append({"source": "conflict", "points": conf_pen, "reason": "cross-group disagreement"})
        port_pen = float(getattr(conviction, "portfolio_penalty", 0.0) or 0.0)
        if port_pen:
            penalties.append({"source": "portfolio", "points": port_pen, "reason": "portfolio heat / risk budget"})
        if bool(getattr(conviction, "penalties_prorated", False)):
            penalties.append({
                "source": "budget_cap",
                "points": float(getattr(conviction, "penalty_budget_cap", 0.0) or 0.0),
                "reason": "penalty budget pro-rated",
            })
        if bool(getattr(conviction, "quality_floor_triggered", False)):
            penalties.append({"source": "quality_floor", "points": 0.0, "reason": "quality floor triggered"})

    if invalidation is not None and getattr(invalidation, "conviction_penalty", 0.0) and conviction is None:
        penalties.append({
            "source": "invalidation",
            "points": float(invalidation.conviction_penalty),
            "reason": "soft invalidation (no conviction stage)",
        })

    # Size multiplier = product of all applied scalars; zero if no sizing row.
    size_mult = 0.0
    if sizing is not None:
        size_mult = float(
            getattr(sizing, "conviction_scalar", 1.0)
            * getattr(sizing, "regime_scalar", 1.0)
            * getattr(sizing, "freshness_scalar", 1.0)
            * getattr(sizing, "risk_gate_scalar", 1.0)
            * getattr(sizing, "equity_curve_scalar", 1.0)
        )

    # AI verdict resolution from the ai_validator journey entry, if any.
    ai_verdict = "skipped"
    if journey:
        for stage in journey.stages:
            if stage.stage == "ai_validator":
                if stage.status in ("rejected", "reject"):
                    ai_verdict = "reject"
                elif stage.status in ("approved", "approve"):
                    ai_verdict = "approve"
                elif stage.status == "skipped":
                    ai_verdict = "skipped"
                else:
                    ai_verdict = stage.status
                break

    # Execution status
    exec_status = "none"
    if outcome_value == "trade_opened":
        exec_status = "executed"
    elif outcome_value == "delayed":
        exec_status = "delayed"
    elif outcome_value in ("rejected", "held", "order_failed"):
        exec_status = "blocked"

    # Hard block = any blocked_by stage that is not a soft-hold or no_signal path
    soft_blockers = {"conviction", "regime_setup_policy", "shadow_mode", "freshness"}
    hard_block = bool(reject_stage and reject_stage not in soft_blockers and outcome_value in ("rejected", "order_failed"))

    return TradeDecision(
        symbol=symbol,
        mode=mode,
        direction=direction,
        setup_type=setup_type,
        outcome=outcome_value,
        reject_stage=reject_stage,
        reject_reason=rejection_reason,
        confidence_raw=raw_conf,
        confidence_adjusted=adjusted_conf,
        conviction_score=conviction_score,
        conviction_decision=conviction_decision,
        penalties=penalties,
        size_multiplier=size_mult,
        hard_block=hard_block,
        ai_verdict=ai_verdict,
        execution_status=exec_status,
        latency_ms=float(getattr(result, "elapsed_ms", 0.0) or 0.0),
        journey=journey,
    )
