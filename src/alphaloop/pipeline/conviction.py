"""
pipeline/conviction.py — Stage 5: Conviction scoring.

Combines structural quality scores, regime context, and penalties into
a single conviction number that maps to a sizing scalar.

Features:
  - Wraps existing scoring/ module (ConfidenceEngine, GroupScorer, etc.)
  - Regime-adjusted group weights and thresholds
  - Cross-group conflict penalty
  - Portfolio context penalty (only dimensions not covered by RiskGate)
  - SOFT_INVALIDATE penalty passthrough
  - Quality floor enforcement
  - Penalty budget cap (prevents silent overblocking)
  - Mandatory score waterfall logging
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.pipeline.types import (
    ConvictionScore,
    InvalidationResult,
    PortfolioContext,
    QualityResult,
    RegimeSnapshot,
)
from alphaloop.scoring.confidence_engine import ConfidenceEngine
from alphaloop.scoring.weights import (
    DEFAULT_CONFIDENCE_THRESHOLDS,
    DEFAULT_GROUP_WEIGHTS,
    load_thresholds,
    load_weights,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Calibration defaults
# ---------------------------------------------------------------------------

MAX_TOTAL_CONVICTION_PENALTY = 50.0   # Never deduct more than this

# Conflict penalty parameters
_CONFLICT_SPREAD_THRESHOLD = 40.0     # No penalty below this spread
_CONFLICT_PENALTY_RATE = 0.75         # Points per unit of spread above threshold
_CONFLICT_PENALTY_CAP = 30.0

# Portfolio penalty parameters (only macro + budget, NOT same-symbol/correlation)
_PORTFOLIO_MACRO_PENALTY = 8.0
_PORTFOLIO_BUDGET_LOW_THRESHOLD = 0.03
_PORTFOLIO_BUDGET_MAX_PENALTY = 20.0
_PORTFOLIO_PENALTY_CAP = 25.0

# Quality floors
_QUALITY_FLOOR_OVERALL = 35.0
_QUALITY_FLOOR_CONTRADICTION_COUNT = 3
_QUALITY_FLOOR_CONTRADICTION_THRESHOLD = 25.0
_QUALITY_FLOOR_MAX_SCORE_MIN = 60.0
_QUALITY_FLOOR_WIN_RATE_MIN = 0.40


class ConvictionScorer:
    """
    Computes a ConvictionScore from structural quality, regime, and penalties.

    Uses the existing scoring/ module infrastructure but adds:
      - penalty budget cap
      - conflict penalty
      - portfolio penalty
      - quality floor enforcement
      - mandatory waterfall logging
    """

    def __init__(
        self,
        strategy_params: dict | None = None,
        *,
        max_penalty: float = MAX_TOTAL_CONVICTION_PENALTY,
    ):
        self.max_penalty = max_penalty

        # Load base weights and thresholds
        self._base_weights = load_weights(strategy_params)
        self._base_thresholds = load_thresholds(strategy_params)

    def score(
        self,
        quality: QualityResult,
        regime: RegimeSnapshot,
        invalidation: InvalidationResult | None = None,
        setup_calibration: float = 1.0,
        raw_confidence: float | None = None,
        ai_weight: float | None = None,
    ) -> ConvictionScore:
        """
        Compute conviction score with full penalty accounting.

        Args:
            quality: Structural quality scores from Stage 4B
            regime: Regime snapshot from Stage 2
            invalidation: Invalidation result from Stage 4A (may carry SOFT penalty)
            setup_calibration: SetupCalibrator factor (from calibrator.py)
            raw_confidence: AI raw confidence (for ai_signal blending)
            ai_weight: AI vs structural weight (0.0-1.0, for ai_signal mode)
        """
        # --- Determine effective weights (regime may override) ---
        weights = dict(self._base_weights)
        if regime.weight_overrides:
            weights.update(regime.weight_overrides)
            # Re-normalize
            total = sum(weights.values())
            if total > 0 and abs(total - 1.0) > 0.001:
                weights = {k: v / total for k, v in weights.items()}

        # --- Compute weighted conviction from group scores ---
        engine = ConfidenceEngine(weights)
        raw_conviction = engine.compute(quality.group_scores)

        # --- Blend with AI confidence if provided (ai_signal mode) ---
        if raw_confidence is not None and ai_weight is not None:
            structural_weight = 1.0 - ai_weight
            raw_conviction = (
                raw_confidence * 100.0 * ai_weight
                + raw_conviction * structural_weight
            )

        # --- Apply setup calibration ---
        raw_conviction = min(raw_conviction * setup_calibration, 100.0)

        # --- Collect penalties ---
        penalties: dict[str, float] = {}

        # Invalidation penalty (from SOFT_INVALIDATE)
        inv_penalty = 0.0
        if invalidation and invalidation.conviction_penalty > 0:
            inv_penalty = invalidation.conviction_penalty
            penalties["invalidation"] = inv_penalty

        # Conflict penalty
        conflict_penalty = self._compute_conflict_penalty(quality.group_scores)
        if conflict_penalty > 0:
            penalties["conflict"] = conflict_penalty

        # Portfolio penalty
        portfolio_penalty = 0.0
        if regime.portfolio_context:
            portfolio_penalty = self._compute_portfolio_penalty(
                regime.portfolio_context
            )
            if portfolio_penalty > 0:
                penalties["portfolio"] = portfolio_penalty

        # --- Apply penalty budget cap ---
        total_penalty = sum(penalties.values())
        prorated = False
        if total_penalty > self.max_penalty:
            scale = self.max_penalty / total_penalty
            penalties = {k: v * scale for k, v in penalties.items()}
            total_penalty = self.max_penalty
            prorated = True

        adjusted_conviction = max(0.0, raw_conviction - total_penalty)

        # --- Apply regime ceiling ---
        adjusted_conviction = min(adjusted_conviction, regime.confidence_ceiling)

        # --- Determine thresholds ---
        thresholds = dict(self._base_thresholds)
        effective_min = thresholds.get("min_entry", 60.0) + regime.min_entry_adjustment
        strong_entry = thresholds.get("strong_entry", 75.0)

        # --- Quality floor checks ---
        # Only enforce when tools actually ran (tool_scores non-empty).
        # When no quality tools are configured, skip floors — conviction
        # from neutral defaults handles the decision via threshold alone.
        floor_triggered = False
        hold_reason = None
        has_tool_scores = bool(quality.tool_scores)

        if has_tool_scores and quality.overall_score < _QUALITY_FLOOR_OVERALL:
            floor_triggered = True
            hold_reason = (
                f"Overall structural score {quality.overall_score:.1f} "
                f"< floor {_QUALITY_FLOOR_OVERALL}"
            )
        elif has_tool_scores and quality.low_score_count >= _QUALITY_FLOOR_CONTRADICTION_COUNT:
            floor_triggered = True
            hold_reason = (
                f"{quality.low_score_count} tools scored < "
                f"{_QUALITY_FLOOR_CONTRADICTION_THRESHOLD} (max {_QUALITY_FLOOR_CONTRADICTION_COUNT})"
            )
        elif has_tool_scores and quality.max_score < _QUALITY_FLOOR_MAX_SCORE_MIN:
            floor_triggered = True
            hold_reason = (
                f"No tool scored above {_QUALITY_FLOOR_MAX_SCORE_MIN} "
                f"(max was {quality.max_score:.1f})"
            )

        # --- Decision ---
        if floor_triggered:
            decision = "HOLD"
            size_scalar = 0.0
            if hold_reason is None:
                hold_reason = "Quality floor triggered"
        elif adjusted_conviction >= strong_entry:
            decision = "TRADE"
            size_scalar = 1.0
            hold_reason = None
        elif adjusted_conviction >= effective_min:
            decision = "TRADE"
            size_scalar = 0.6
            hold_reason = None
        else:
            decision = "HOLD"
            size_scalar = 0.0
            hold_reason = (
                f"Conviction {adjusted_conviction:.1f} < "
                f"min_entry {effective_min:.1f}"
            )

        # --- Build reasoning / waterfall ---
        reasoning = self._build_waterfall(
            raw_conviction=raw_conviction,
            quality=quality,
            penalties=penalties,
            total_penalty=total_penalty,
            prorated=prorated,
            adjusted_conviction=adjusted_conviction,
            effective_min=effective_min,
            decision=decision,
            floor_triggered=floor_triggered,
            hold_reason=hold_reason,
            regime=regime,
        )

        result = ConvictionScore(
            score=round(adjusted_conviction, 2),
            normalized=round(adjusted_conviction / 100.0, 4),
            size_scalar=round(size_scalar, 2),
            decision=decision,
            hold_reason=hold_reason,
            regime_min_entry=round(effective_min, 1),
            regime_ceiling=regime.confidence_ceiling,
            group_contributions={
                k: round(v, 1) for k, v in quality.group_scores.items()
            },
            conflict_penalty=round(penalties.get("conflict", 0), 1),
            invalidation_penalty=round(penalties.get("invalidation", 0), 1),
            portfolio_penalty=round(penalties.get("portfolio", 0), 1),
            total_penalty=round(total_penalty, 1),
            penalty_budget_used=round(total_penalty, 1),
            penalty_budget_cap=self.max_penalty,
            penalties_prorated=prorated,
            setup_calibration=round(setup_calibration, 3),
            quality_floor_triggered=floor_triggered,
            reasoning=reasoning,
        )

        # Mandatory waterfall log
        logger.info("[WATERFALL] %s", reasoning)

        return result

    # ------------------------------------------------------------------
    # Penalty calculators
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_conflict_penalty(group_scores: dict[str, float]) -> float:
        """Penalise when feature groups strongly disagree."""
        scores = [v for v in group_scores.values() if v is not None]
        if len(scores) < 2:
            return 0.0

        spread = max(scores) - min(scores)
        if spread < _CONFLICT_SPREAD_THRESHOLD:
            return 0.0

        penalty = (spread - _CONFLICT_SPREAD_THRESHOLD) * _CONFLICT_PENALTY_RATE
        return min(penalty, _CONFLICT_PENALTY_CAP)

    @staticmethod
    def _compute_portfolio_penalty(ctx: PortfolioContext) -> float:
        """
        Only penalise dimensions NOT handled by RiskGate.

        RiskGate handles: same-symbol stacking, high correlation.
        Conviction handles: macro exposure clustering, low remaining budget.
        """
        penalty = 0.0

        # Macro clustering (RiskGate doesn't check this)
        if ctx.macro_exposure is not None:
            penalty += _PORTFOLIO_MACRO_PENALTY

        # Risk budget running low (conviction makes system more selective
        # before hitting hard cap)
        if ctx.risk_budget_remaining_pct < _PORTFOLIO_BUDGET_LOW_THRESHOLD:
            budget_pct = max(0.0, ctx.risk_budget_remaining_pct)
            ratio = budget_pct / _PORTFOLIO_BUDGET_LOW_THRESHOLD
            budget_penalty = _PORTFOLIO_BUDGET_MAX_PENALTY - (
                ratio * (_PORTFOLIO_BUDGET_MAX_PENALTY - 5.0)
            )
            penalty += max(5.0, budget_penalty)

        return min(penalty, _PORTFOLIO_PENALTY_CAP)

    # ------------------------------------------------------------------
    # Waterfall logger
    # ------------------------------------------------------------------

    @staticmethod
    def _build_waterfall(
        *,
        raw_conviction: float,
        quality: QualityResult,
        penalties: dict[str, float],
        total_penalty: float,
        prorated: bool,
        adjusted_conviction: float,
        effective_min: float,
        decision: str,
        floor_triggered: bool,
        hold_reason: str | None,
        regime: RegimeSnapshot,
    ) -> str:
        lines = [
            f"structural_quality: {quality.overall_score:.1f}",
            f"weighted_conviction: {raw_conviction:.1f}",
        ]
        for name, val in sorted(penalties.items()):
            lines.append(f"{name}_penalty: -{val:.1f}")
        lines.append(
            f"penalty_budget: {total_penalty:.1f}/{MAX_TOTAL_CONVICTION_PENALTY}"
            + (" [PRORATED]" if prorated else "")
        )
        lines.append(f"adjusted_conviction: {adjusted_conviction:.1f}")
        lines.append(
            f"regime: {regime.regime} | min_entry={effective_min:.1f} "
            f"| ceiling={regime.confidence_ceiling:.0f}"
        )
        if floor_triggered:
            lines.append(f"quality_floor: TRIGGERED — {hold_reason}")
        lines.append(f"decision: {decision}")
        if hold_reason and not floor_triggered:
            lines.append(f"hold_reason: {hold_reason}")

        return " | ".join(lines)
