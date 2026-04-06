"""
pipeline/orchestrator.py — 8-stage institutional pipeline coordinator.

Runs the unified pipeline for all three signal modes (algo_only, algo_ai,
ai_signal).  Modes differ only in Stage 3 (signal generator) and Stage 6
(AI validator presence).

Every cycle resolves to exactly one CycleOutcome:
  NO_SIGNAL | REJECTED | HELD | DELAYED | TRADE_OPENED | ORDER_FAILED

Includes mandatory score waterfall logging and shadow-mode support.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from alphaloop.pipeline.types import (
    CandidateSignal,
    CandidateJourney,
    ConvictionScore,
    CycleOutcome,
    DirectionHypothesis,
    ExecutionGuardResult,
    InvalidationResult,
    MarketGateResult,
    QualityResult,
    RegimeSnapshot,
    RiskGateResult,
    SizingDecision,
)
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.quality import StructuralQuality
from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.risk_gate import RiskGateRunner
from alphaloop.pipeline.freshness import compute_freshness

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Full pipeline outcome with stage-by-stage results."""

    outcome: CycleOutcome
    market_gate: MarketGateResult | None = None
    regime: RegimeSnapshot | None = None
    hypothesis: DirectionHypothesis | None = None
    signal: CandidateSignal | None = None
    invalidation: InvalidationResult | None = None
    quality: QualityResult | None = None
    conviction: ConvictionScore | None = None
    risk_gate: RiskGateResult | None = None
    execution_guard: ExecutionGuardResult | None = None
    sizing: SizingDecision | None = None
    elapsed_ms: float = 0.0
    rejection_reason: str | None = None
    construction_source: str | None = None
    journey: CandidateJourney = field(default_factory=CandidateJourney)


class PipelineOrchestrator:
    """
    Coordinates the institutional pipeline.

    Stages:
      1. MarketGate              -> pass / REJECT
      2. RegimeClassifier        -> parameterise (never blocks)
      3. DirectionHypothesis     -> hypothesis / NO_SIGNAL
      3B. TradeConstruction      -> CandidateSignal / NO_SIGNAL  (constraint-first)
      4A. StructuralInvalidation -> REJECT / penalty / pass  (safety-net)
      4B. StructuralQuality      -> soft scores (never blocks)
      5. ConvictionScorer        -> TRADE / HOLD
      6. [AI Validator]          -> algo_ai / ai_signal only
      7. RiskGate                -> pass / REJECT
      8. ExecutionGuard          -> EXECUTE / DELAY / REJECT
    """

    def __init__(
        self,
        *,
        market_gate: MarketGate | None = None,
        regime_classifier: RegimeClassifier | None = None,
        trade_constructor=None,
        invalidator: StructuralInvalidator | None = None,
        quality_scorer: StructuralQuality | None = None,
        conviction_scorer: ConvictionScorer | None = None,
        risk_gate: RiskGateRunner | None = None,
        execution_guard: ExecutionGuardRunner | None = None,
        ai_validator=None,
        shadow_mode: bool = False,
        enabled_tools: dict[str, bool] | None = None,
        hypothesis_tools: list | None = None,
    ):
        self.market_gate = market_gate or MarketGate()
        self.regime_classifier = regime_classifier or RegimeClassifier()
        self.trade_constructor = trade_constructor  # pipeline/construction.TradeConstructor
        self.invalidator = invalidator or StructuralInvalidator()
        self.quality_scorer = quality_scorer
        self.conviction_scorer = conviction_scorer or ConvictionScorer()
        self.risk_gate = risk_gate or RiskGateRunner()
        self.execution_guard = execution_guard or ExecutionGuardRunner()
        self.ai_validator = ai_validator
        self.shadow_mode = shadow_mode
        self.enabled_tools = enabled_tools or {}
        self.hypothesis_tools: list = hypothesis_tools or []

    async def run(
        self,
        context,
        signal_generator,
        *,
        symbol: str = "",
        mode: str = "algo_only",
        setup_calibration: float = 1.0,
        ai_weight: float | None = None,
    ) -> PipelineResult:
        """
        Execute the full 8-stage pipeline.

        Args:
            context: MarketContext for this cycle
            signal_generator: Callable(context, regime) -> DirectionHypothesis | CandidateSignal | None
            symbol: Trading symbol
            mode: "algo_only" | "algo_ai" | "ai_signal"
            setup_calibration: SetupCalibrator factor
            ai_weight: AI vs structural blend weight (ai_signal mode)
        """
        t0 = time.monotonic()
        result = PipelineResult(outcome=CycleOutcome.NO_SIGNAL)

        try:
            # ============================================================
            # Stage 1: MarketGate
            # ============================================================
            gate = await self.market_gate.check(context)
            result.market_gate = gate
            self._record_stage(
                result,
                "market_gate",
                "passed" if gate.tradeable else "blocked",
                detail=gate.block_reason or ("tradeable" if gate.tradeable else ""),
                blocked_by=gate.blocked_by,
                payload={
                    "data_quality": gate.data_quality,
                    "spread_ratio": gate.spread_ratio,
                    "bars_available": gate.bars_available,
                },
            )

            if not gate.tradeable:
                result.outcome = CycleOutcome.REJECTED
                result.rejection_reason = gate.block_reason
                return self._finalise(result, t0)

            # ============================================================
            # Stage 2: RegimeClassifier
            # ============================================================
            regime = await self.regime_classifier.classify(context)
            result.regime = regime
            self._record_stage(
                result,
                "regime",
                "classified",
                detail=regime.regime,
                payload={
                    "macro_regime": regime.macro_regime,
                    "volatility_band": regime.volatility_band,
                    "allowed_setups": list(regime.allowed_setups),
                    "session_quality": regime.session_quality,
                    "size_multiplier": regime.size_multiplier,
                },
            )

            # ============================================================
            # Stage 3: Direction Hypothesis / Signal Generator
            # ============================================================
            # Run hypothesis tools (ema_crossover, macd_filter, rsi_feature,
            # fast_fingers) before calling the signal generator so their results
            # are available in context.tool_results for the T5 prompt tier.
            # Direction-dependent tools auto-skip (direction unknown at this point).
            if self.hypothesis_tools:
                _tool_results = []
                for _tool in self.hypothesis_tools:
                    try:
                        _tr = await _tool.timed_run(context)
                        _tool_results.append({
                            "tool_name": _tr.tool_name,
                            "passed": _tr.passed,
                            "reason": _tr.reason,
                            "bias": _tr.bias,
                            "size_modifier": _tr.size_modifier,
                        })
                    except Exception as _exc:
                        logger.warning(
                            "[Pipeline] Hypothesis tool %s error: %s",
                            getattr(_tool, "name", "?"), _exc,
                        )
                # Attach to context so signal engines can read for T5 tier
                try:
                    context.tool_results = _tool_results
                except AttributeError:
                    pass  # read-only context (tests) — skip gracefully

            raw_output = await signal_generator(context, regime)
            if raw_output is None:
                self._record_stage(
                    result,
                    "signal",
                    "no_signal",
                    detail="signal generator returned None",
                )
                result.outcome = CycleOutcome.NO_SIGNAL
                return self._finalise(result, t0)

            # ============================================================
            # Stage 3B: Trade Construction (constraint-first)
            # ============================================================
            # If signal_generator returned a DirectionHypothesis AND we
            # have a TradeConstructor, construct the trade from structure.
            # Otherwise fall back to treating raw_output as a
            # CandidateSignal (backward compat for tests / legacy).
            signal: CandidateSignal | None = None

            if isinstance(raw_output, DirectionHypothesis) and self.trade_constructor:
                result.hypothesis = raw_output
                self._record_stage(
                    result,
                    "signal",
                    "hypothesis_generated",
                    detail=raw_output.setup_tag,
                    payload={
                        "direction": raw_output.direction,
                        "confidence": raw_output.confidence,
                        "source_names": raw_output.source_names,
                    },
                )

                # Extract bid/ask from context.price (SimpleNamespace or dict)
                price_data = getattr(context, "price", None)
                if price_data is None and isinstance(context, dict):
                    price_data = context.get("current_price", {})
                if price_data is None:
                    price_data = {}
                bid = float(getattr(price_data, "bid", None) or (price_data.get("bid", 0) if isinstance(price_data, dict) else 0))
                ask = float(getattr(price_data, "ask", None) or (price_data.get("ask", 0) if isinstance(price_data, dict) else 0))

                # Extract M15 indicators — context may be AttrDict or Pydantic model
                # AttrDict from loop._build_context: context.indicators["M15"]
                # Also try: context.timeframes["M15"]["indicators"]
                m15_ind = {}
                _indicators = getattr(context, "indicators", None)
                if isinstance(_indicators, dict) and "M15" in _indicators:
                    m15_ind = _indicators["M15"]
                elif isinstance(context, dict):
                    m15_ind = context.get("timeframes", {}).get("M15", {}).get("indicators", {})

                atr_val = float(m15_ind.get("atr", 0) or 0)

                construction = self.trade_constructor.construct(
                    raw_output, bid, ask, m15_ind, atr_val,
                )

                if construction.signal is None:
                    self._record_stage(
                        result,
                        "construction",
                        "rejected",
                        detail=construction.rejection_reason or "no trade constructed",
                        blocked_by="construction",
                        payload={
                            "candidates_considered": construction.candidates_considered,
                        },
                    )
                    result.outcome = CycleOutcome.NO_CONSTRUCTION
                    result.rejection_reason = (
                        f"no trade constructed: {construction.rejection_reason}"
                    )
                    result.construction_source = ""
                    logger.info(
                        "[Pipeline] No construction: %s (candidates=%d)",
                        construction.rejection_reason,
                        construction.candidates_considered,
                    )
                    return self._finalise(result, t0)

                signal = construction.signal
                result.construction_source = construction.sl_source
                self._record_stage(
                    result,
                    "construction",
                    "constructed",
                    detail=construction.sl_source,
                    payload={
                        "direction": signal.direction,
                        "setup_type": signal.setup_type,
                        "rr_ratio": signal.rr_ratio,
                        "candidates_considered": construction.candidates_considered,
                    },
                )

                # --- Construction validation plugins (swing_structure, fvg_guard, bos_guard) ---
                # Run here (not inside construct()) because plugins need full context.
                # Disagreement is a warning only — SL/TP are already structure-derived.
                if self.trade_constructor._tools:
                    for _ctool in self.trade_constructor._tools:
                        try:
                            _cr = await _ctool.timed_run(context)
                            if not _cr.passed:
                                logger.warning(
                                    "[Pipeline] Construction plugin disagreement — %s: %s",
                                    _cr.tool_name, _cr.reason,
                                )
                                construction.structural_warnings.append(
                                    f"{_cr.tool_name}: {_cr.reason}"
                                )
                        except Exception as _ce:
                            logger.warning(
                                "[Pipeline] Construction tool %s error: %s",
                                getattr(_ctool, "name", "?"), _ce,
                            )

            elif isinstance(raw_output, DirectionHypothesis):
                # DirectionHypothesis but no TradeConstructor — cannot proceed
                self._record_stage(
                    result,
                    "construction",
                    "rejected",
                    detail="no TradeConstructor configured",
                    blocked_by="construction",
                )
                result.outcome = CycleOutcome.NO_SIGNAL
                result.rejection_reason = "no TradeConstructor configured"
                return self._finalise(result, t0)

            else:
                # Backward compat: raw_output is already a CandidateSignal
                signal = raw_output
                self._record_stage(
                    result,
                    "signal",
                    "signal_generated",
                    detail=signal.setup_type,
                    payload={
                        "direction": signal.direction,
                        "rr_ratio": signal.rr_ratio,
                        "raw_confidence": signal.raw_confidence,
                    },
                )

            result.signal = signal

            # Check setup vs regime allowed list
            if regime.allowed_setups and signal.setup_type not in regime.allowed_setups:
                self._record_stage(
                    result,
                    "setup_policy",
                    "held",
                    detail=(
                        f"Setup '{signal.setup_type}' not allowed in "
                        f"'{regime.regime}' regime"
                    ),
                    blocked_by="regime_setup_policy",
                    payload={"allowed_setups": list(regime.allowed_setups)},
                )
                result.outcome = CycleOutcome.HELD
                result.rejection_reason = (
                    f"Setup '{signal.setup_type}' not allowed in "
                    f"'{regime.regime}' regime"
                )
                logger.info(
                    "[Pipeline] HELD: %s", result.rejection_reason
                )
                return self._finalise(result, t0)

            # Set direction on context for direction-dependent tools
            if hasattr(context, "trade_direction"):
                context.trade_direction = signal.direction

            # ============================================================
            # Stage 4A: Structural Invalidation
            # ============================================================
            inv = await self.invalidator.validate(
                signal, regime, context, enabled_tools=self.enabled_tools,
            )
            result.invalidation = inv
            self._record_stage(
                result,
                "invalidation",
                {
                    "PASS": "passed",
                    "SOFT_INVALIDATE": "soft_invalidated",
                    "HARD_INVALIDATE": "hard_invalidated",
                }.get(inv.severity, inv.severity.lower()),
                detail=", ".join(f.reason for f in inv.failures) if inv.failures else inv.severity,
                blocked_by="invalidation" if inv.severity == "HARD_INVALIDATE" else None,
                payload={
                    "severity": inv.severity,
                    "conviction_penalty": inv.conviction_penalty,
                    "checks_run": list(inv.checks_run),
                },
            )

            if inv.severity == "HARD_INVALIDATE":
                result.outcome = CycleOutcome.REJECTED
                result.rejection_reason = (
                    f"Structural invalidation: "
                    f"{[f.reason for f in inv.failures]}"
                )
                return self._finalise(result, t0)

            # ============================================================
            # Stage 4B: Structural Quality
            # ============================================================
            quality = QualityResult()
            if self.quality_scorer:
                quality = await self.quality_scorer.evaluate(
                    context,
                    weights=regime.weight_overrides or None,
                )
            result.quality = quality
            self._record_stage(
                result,
                "quality",
                "scored",
                detail=f"overall={quality.overall_score:.1f}",
                payload={
                    "overall_score": quality.overall_score,
                    "group_scores": dict(quality.group_scores),
                    "low_score_count": quality.low_score_count,
                },
            )

            # ============================================================
            # Stage 5: Conviction Scorer
            # ============================================================
            raw_conf = None
            if mode == "ai_signal":
                raw_conf = signal.raw_confidence

            conviction = self.conviction_scorer.score(
                quality=quality,
                regime=regime,
                invalidation=inv,
                setup_calibration=setup_calibration,
                raw_confidence=raw_conf,
                ai_weight=ai_weight,
            )
            result.conviction = conviction
            self._record_stage(
                result,
                "conviction",
                "trade" if conviction.decision == "TRADE" else "held",
                detail=conviction.hold_reason or conviction.reasoning,
                blocked_by="conviction" if conviction.decision == "HOLD" else None,
                payload={
                    "score": conviction.score,
                    "size_scalar": conviction.size_scalar,
                    "quality_floor_triggered": conviction.quality_floor_triggered,
                },
            )

            if conviction.decision == "HOLD":
                result.outcome = CycleOutcome.HELD
                result.rejection_reason = conviction.hold_reason
                return self._finalise(result, t0)

            # ============================================================
            # Stage 6: AI Validator (algo_ai and ai_signal — Phase 4C)
            # Invariant 6: no AI-originated trade bypasses validation
            # ============================================================
            if mode in ("algo_ai", "ai_signal") and self.ai_validator:
                validated = await self.ai_validator.validate(
                    signal, regime, quality, conviction, context
                )
                if validated is None:
                    self._record_stage(
                        result,
                        "ai_validator",
                        "rejected",
                        detail="AI validator rejected",
                        blocked_by="ai_validator",
                    )
                    result.outcome = CycleOutcome.REJECTED
                    result.rejection_reason = "AI validator rejected"
                    return self._finalise(result, t0)
                # AI may have adjusted the signal — update reference
                result.signal = validated
                self._record_stage(
                    result,
                    "ai_validator",
                    "approved",
                    detail="validated",
                    payload={
                        "direction": validated.direction,
                        "raw_confidence": validated.raw_confidence,
                    },
                )
            else:
                self._record_stage(
                    result,
                    "ai_validator",
                    "skipped",
                    detail="AI validation not active for this cycle",
                )

            # ============================================================
            # Stage 7: Risk Gate
            # ============================================================
            risk = await self.risk_gate.check(signal, context, symbol=symbol)
            result.risk_gate = risk
            self._record_stage(
                result,
                "risk_gate",
                "passed" if risk.allowed else "blocked",
                detail=risk.block_reason or "",
                blocked_by="risk_gate" if not risk.allowed else None,
                payload={
                    "size_modifier": risk.size_modifier,
                    "equity_curve_scalar": risk.equity_curve_scalar,
                    "risk_utilization": risk.risk_utilization,
                },
            )

            if not risk.allowed:
                result.outcome = CycleOutcome.REJECTED
                result.rejection_reason = risk.block_reason
                return self._finalise(result, t0)

            # ============================================================
            # Stage 8: Execution Guard
            # ============================================================
            exec_result = await self.execution_guard.check(
                signal, context, symbol=symbol,
                enabled_tools=self.enabled_tools,
            )
            result.execution_guard = exec_result
            self._record_stage(
                result,
                "execution_guard",
                exec_result.action.lower(),
                detail=exec_result.block_reason or exec_result.delay_reason or "",
                blocked_by=exec_result.blocked_by,
                payload={
                    "delay_candles": exec_result.delay_candles,
                    "warnings": list(exec_result.warnings),
                },
            )

            if exec_result.action == "BLOCK":
                result.outcome = CycleOutcome.REJECTED
                result.rejection_reason = exec_result.block_reason
                return self._finalise(result, t0)

            if exec_result.action == "DELAY":
                self.execution_guard.queue_delay(
                    symbol, signal, exec_result.delay_reason or ""
                )
                result.outcome = CycleOutcome.DELAYED
                result.rejection_reason = exec_result.delay_reason
                return self._finalise(result, t0)

            # ============================================================
            # Sizing
            # ============================================================
            freshness = compute_freshness(
                signal,
                current_price=self._get_current_price(context),
                atr=self._get_atr(context),
            )

            if freshness <= 0:
                self._record_stage(
                    result,
                    "freshness",
                    "held",
                    detail="Signal too stale (freshness=0)",
                    blocked_by="freshness",
                )
                result.outcome = CycleOutcome.HELD
                result.rejection_reason = "Signal too stale (freshness=0)"
                return self._finalise(result, t0)

            sizing = SizingDecision(
                conviction_scalar=conviction.size_scalar,
                regime_scalar=regime.size_multiplier,
                freshness_scalar=round(freshness, 4),
                risk_gate_scalar=risk.size_modifier,
                equity_curve_scalar=risk.equity_curve_scalar,
            )
            result.sizing = sizing
            self._record_stage(
                result,
                "sizing",
                "computed",
                detail=f"freshness={freshness:.4f}",
                payload={
                    "conviction_scalar": sizing.conviction_scalar,
                    "regime_scalar": sizing.regime_scalar,
                    "freshness_scalar": sizing.freshness_scalar,
                    "risk_gate_scalar": sizing.risk_gate_scalar,
                    "equity_curve_scalar": sizing.equity_curve_scalar,
                },
            )

            # ============================================================
            # Shadow mode gate — signal fully constructed but NOT executed.
            # All stages ran so the waterfall log captures the full breakdown.
            # ============================================================
            if self.shadow_mode:
                self._record_stage(
                    result,
                    "shadow_mode",
                    "held",
                    detail="signal generated but not executed",
                    blocked_by="shadow_mode",
                )
                result.outcome = CycleOutcome.HELD
                result.rejection_reason = "shadow_mode: signal generated but not executed"
                logger.info(
                    "[Pipeline] SHADOW %s %s conf=%.2f — not executing",
                    signal.direction,
                    signal.setup_type,
                    signal.raw_confidence or 0.0,
                )
                return self._finalise(result, t0)

            result.outcome = CycleOutcome.TRADE_OPENED

            return self._finalise(result, t0)

        except Exception as exc:
            logger.error("[Pipeline] Unhandled error: %s", exc, exc_info=True)
            self._record_stage(
                result,
                "pipeline",
                "error",
                detail=str(exc),
                blocked_by="pipeline_error",
            )
            result.outcome = CycleOutcome.REJECTED
            result.rejection_reason = f"Pipeline error: {exc}"
            return self._finalise(result, t0)

    # ------------------------------------------------------------------
    # Delayed signal re-evaluation
    # ------------------------------------------------------------------

    async def check_delayed(
        self,
        context,
        symbol: str,
    ) -> PipelineResult | None:
        """
        Re-evaluate a delayed signal from the execution guard queue.

        Returns PipelineResult if the signal should proceed, None if still
        delayed or expired.
        """
        ds = self.execution_guard.tick_delay(symbol)
        if ds is None:
            return None

        # Re-check execution guards with the delayed signal
        exec_result = await self.execution_guard.check(
            ds.signal, context, symbol=symbol
        )

        if exec_result.action == "EXECUTE":
            self.execution_guard.clear_delay(symbol)

            # Apply freshness decay
            freshness = compute_freshness(
                ds.signal,
                current_price=self._get_current_price(context),
                atr=self._get_atr(context),
                candles_elapsed=ds.candles_waited,
            )

            if freshness <= 0:
                logger.info(
                    "[Pipeline] Delayed signal expired (freshness=0) after %d candles",
                    ds.candles_waited,
                )
                return None

            return PipelineResult(
                outcome=CycleOutcome.TRADE_OPENED,
                signal=ds.signal,
                sizing=SizingDecision(freshness_scalar=round(freshness, 4)),
            )

        if exec_result.action == "BLOCK":
            self.execution_guard.clear_delay(symbol)
            return PipelineResult(
                outcome=CycleOutcome.REJECTED,
                rejection_reason=exec_result.block_reason,
            )

        # Still DELAY — continue waiting
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _finalise(result: PipelineResult, t0: float) -> PipelineResult:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        result.journey.finalize(
            outcome=result.outcome.value,
            rejection_reason=result.rejection_reason,
        )
        logger.info(
            "[Pipeline] %s in %.1fms%s",
            result.outcome.value,
            result.elapsed_ms,
            f" — {result.rejection_reason}" if result.rejection_reason else "",
        )
        return result

    @staticmethod
    def _record_stage(
        result: PipelineResult,
        stage: str,
        status: str,
        *,
        detail: str = "",
        blocked_by: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        result.journey.add_stage(
            stage,
            status,
            detail=detail,
            blocked_by=blocked_by,
            payload=payload,
        )

    @staticmethod
    def _get_current_price(context) -> float:
        price = getattr(context, "price", None)
        if price is None:
            return 0.0
        bid = float(getattr(price, "bid", 0) or 0)
        ask = float(getattr(price, "ask", 0) or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return bid or ask or 0.0

    @staticmethod
    def _get_atr(context) -> float:
        indicators = getattr(context, "indicators", {})
        m15 = indicators.get("M15", {})
        return float(m15.get("atr", 1.0) or 1.0)
