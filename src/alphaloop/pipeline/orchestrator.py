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

            if not gate.tradeable:
                result.outcome = CycleOutcome.REJECTED
                result.rejection_reason = gate.block_reason
                return self._finalise(result, t0)

            # ============================================================
            # Stage 2: RegimeClassifier
            # ============================================================
            regime = await self.regime_classifier.classify(context)
            result.regime = regime

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
                result.outcome = CycleOutcome.NO_SIGNAL
                result.rejection_reason = "no TradeConstructor configured"
                return self._finalise(result, t0)

            else:
                # Backward compat: raw_output is already a CandidateSignal
                signal = raw_output

            result.signal = signal

            # Check setup vs regime allowed list
            if regime.allowed_setups and signal.setup_type not in regime.allowed_setups:
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
                    result.outcome = CycleOutcome.REJECTED
                    result.rejection_reason = "AI validator rejected"
                    return self._finalise(result, t0)
                # AI may have adjusted the signal — update reference
                result.signal = validated

            # ============================================================
            # Stage 7: Risk Gate
            # ============================================================
            risk = await self.risk_gate.check(signal, context, symbol=symbol)
            result.risk_gate = risk

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

            # ============================================================
            # Shadow mode gate — signal fully constructed but NOT executed.
            # All stages ran so the waterfall log captures the full breakdown.
            # ============================================================
            if self.shadow_mode:
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
        logger.info(
            "[Pipeline] %s in %.1fms%s",
            result.outcome.value,
            result.elapsed_ms,
            f" — {result.rejection_reason}" if result.rejection_reason else "",
        )
        return result

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
