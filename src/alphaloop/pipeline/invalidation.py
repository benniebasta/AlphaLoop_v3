"""
pipeline/invalidation.py — Stage 4A: Structural invalidation.

Severity-based checks that CAN block (HARD_INVALIDATE) or penalise
(SOFT_INVALIDATE) a candidate signal.

What is checked depends on the signal's setup_type.  Universal checks
(SL direction, R:R, SL distance) run for every setup.  Strategy-specific
checks consult the invalidation matrix.

All numeric thresholds are initial calibration defaults, overridable via
strategy config under the ``pipeline_v4`` key.
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.core.normalization import normalize_distance
from alphaloop.pipeline.types import (
    CandidateSignal,
    InvalidationFailure,
    InvalidationResult,
    RegimeSnapshot,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Calibration defaults (strategy-overridable)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "rr_hard_min": 1.0,
    "rr_soft_min": 1.5,
    "confidence_hard_min": 0.30,
    "sl_min_points": 20.0,
    "sl_max_points": 300.0,
    "sl_boundary_tolerance_pct": 0.10,  # within 10% of boundary = SOFT
    "bos_weak_atr": 0.2,
    "ema200_hard_atr": 1.0,
    "ema200_soft_atr": 0.3,
    "bb_hard_threshold": 0.65,    # %B for range_bounce invalidation
    "bb_soft_mid_low": 0.45,      # mid-band ambiguity zone
    "bb_soft_mid_high": 0.55,
}

# ---------------------------------------------------------------------------
# Strategy-type invalidation matrix
# ---------------------------------------------------------------------------
# For each (setup_type, check_name) pair, define which severity applies.
# Absence means the check does not apply to that setup type.

_MATRIX: dict[str, dict[str, str]] = {
    "breakout": {
        "bos_required": "strategy",
        "ema200_alignment": "strategy",
    },
    "pullback": {
        "ema200_alignment": "strategy",
        "swing_alignment": "strategy",
    },
    "continuation": {
        "bos_required": "strategy",
        "swing_alignment": "strategy",
    },
    "reversal": {
        "exhaustion_required": "strategy",
    },
    "range_bounce": {
        "swing_ranging": "strategy",
        "bollinger_position": "strategy",
    },
}


class StructuralInvalidator:
    """
    Runs universal + strategy-type-dependent invalidation checks.

    Returns an InvalidationResult with severity:
      HARD_INVALIDATE — reject immediately
      SOFT_INVALIDATE — apply conviction penalty, proceed
      PASS            — no issues
    """

    def __init__(self, cfg: dict[str, Any] | None = None, tools: list | None = None):
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self._tools: list = tools or []  # liq_vacuum_guard, vwap_guard

    async def validate(
        self,
        signal: CandidateSignal,
        regime: RegimeSnapshot,
        context,
        *,
        enabled_tools: dict[str, bool] | None = None,
    ) -> InvalidationResult:
        """Run all applicable invalidation checks.

        When *enabled_tools* is provided, strategy-type-dependent checks
        whose corresponding tool is toggled OFF are skipped.
        """

        failures: list[InvalidationFailure] = []
        checks_run: list[str] = []
        tools = enabled_tools or {}

        # --- Universal checks ---
        self._check_sl_direction(signal, failures, checks_run)
        self._check_rr_ratio(signal, failures, checks_run)
        self._check_sl_distance(signal, failures, checks_run, context)
        self._check_confidence_floor(signal, failures, checks_run)
        self._check_regime_setup(signal, regime, failures, checks_run)

        # --- Strategy-type-dependent checks ---
        # Each check is skipped when its matching tool is explicitly OFF.
        matrix = _MATRIX.get(signal.setup_type, {})
        indicators = getattr(context, "indicators", {})
        m15 = indicators.get("M15", {})

        if "bos_required" in matrix and tools.get("bos_guard", True):
            self._check_bos(signal, m15, failures, checks_run)

        if "ema200_alignment" in matrix and tools.get("ema200_filter", True):
            self._check_ema200(signal, m15, context, failures, checks_run)

        if "swing_alignment" in matrix and tools.get("swing_structure", True):
            self._check_swing(signal, m15, failures, checks_run)

        if "exhaustion_required" in matrix and tools.get("fast_fingers", True):
            self._check_exhaustion(signal, m15, failures, checks_run)

        if "swing_ranging" in matrix and tools.get("swing_structure", True):
            self._check_swing_ranging(m15, failures, checks_run)

        if "bollinger_position" in matrix and tools.get("bollinger_filter", True):
            self._check_bollinger(signal, m15, failures, checks_run)

        # --- Injected plugin checks (liq_vacuum_guard, vwap_guard) ---
        # These are called via timed_run() instead of reading raw indicators.
        for tool in self._tools:
            try:
                tool_result = await tool.timed_run(context)
                if not tool_result.passed:
                    severity = (
                        "HARD_INVALIDATE"
                        if tool_result.severity == "block"
                        else "SOFT_INVALIDATE"
                    )
                    failures.append(
                        InvalidationFailure(
                            check_name=tool_result.tool_name,
                            severity=severity,
                            reason=tool_result.reason,
                        )
                    )
                    checks_run.append(tool_result.tool_name)
            except Exception as exc:
                logger.warning(
                    "[Invalidation] Tool %s error: %s",
                    getattr(tool, "name", "?"), exc,
                )

        # --- Aggregate ---
        hard = [f for f in failures if f.severity == "HARD_INVALIDATE"]
        soft = [f for f in failures if f.severity == "SOFT_INVALIDATE"]

        if hard:
            severity = "HARD_INVALIDATE"
            penalty = 0.0  # N/A — signal rejected
        elif soft:
            severity = "SOFT_INVALIDATE"
            # Penalty scales with number and severity of soft failures
            penalty = min(50.0, sum(self._soft_penalty(f) for f in soft))
        else:
            severity = "PASS"
            penalty = 0.0

        result = InvalidationResult(
            severity=severity,
            failures=failures,
            conviction_penalty=penalty,
            checks_run=checks_run,
            setup_type=signal.setup_type,
        )

        if severity != "PASS":
            logger.info(
                "[Invalidation] %s | %d failures | penalty=%.1f | %s",
                severity,
                len(failures),
                penalty,
                [f.check_name for f in failures],
            )

        return result

    # ------------------------------------------------------------------
    # Universal checks
    # ------------------------------------------------------------------

    def _check_sl_direction(
        self,
        sig: CandidateSignal,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("sl_direction")
        entry_mid = (sig.entry_zone[0] + sig.entry_zone[1]) / 2

        if sig.direction == "BUY" and sig.stop_loss >= entry_mid:
            failures.append(
                InvalidationFailure(
                    "sl_direction",
                    "HARD_INVALIDATE",
                    f"BUY but SL ({sig.stop_loss}) >= entry ({entry_mid:.2f})",
                )
            )
        elif sig.direction == "SELL" and sig.stop_loss <= entry_mid:
            failures.append(
                InvalidationFailure(
                    "sl_direction",
                    "HARD_INVALIDATE",
                    f"SELL but SL ({sig.stop_loss}) <= entry ({entry_mid:.2f})",
                )
            )

        # TP direction
        checks.append("tp_direction")
        if sig.take_profit:
            tp1 = sig.take_profit[0]
            if sig.direction == "BUY" and tp1 <= entry_mid:
                failures.append(
                    InvalidationFailure(
                        "tp_direction",
                        "HARD_INVALIDATE",
                        f"BUY but TP1 ({tp1}) <= entry ({entry_mid:.2f})",
                    )
                )
            elif sig.direction == "SELL" and tp1 >= entry_mid:
                failures.append(
                    InvalidationFailure(
                        "tp_direction",
                        "HARD_INVALIDATE",
                        f"SELL but TP1 ({tp1}) >= entry ({entry_mid:.2f})",
                    )
                )

    def _check_rr_ratio(
        self,
        sig: CandidateSignal,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("rr_ratio")
        rr = sig.rr_ratio
        if rr <= 0:
            # Compute if not provided
            entry_mid = (sig.entry_zone[0] + sig.entry_zone[1]) / 2
            risk = abs(entry_mid - sig.stop_loss)
            if risk > 0 and sig.take_profit:
                reward = abs(sig.take_profit[0] - entry_mid)
                rr = reward / risk

        hard_min = self.cfg["rr_hard_min"]
        soft_min = self.cfg["rr_soft_min"]

        if rr < hard_min:
            failures.append(
                InvalidationFailure(
                    "rr_ratio",
                    "HARD_INVALIDATE",
                    f"R:R {rr:.2f} < hard minimum {hard_min}",
                    measured_value=rr,
                    threshold=hard_min,
                )
            )
        elif rr < soft_min:
            failures.append(
                InvalidationFailure(
                    "rr_ratio",
                    "SOFT_INVALIDATE",
                    f"R:R {rr:.2f} < target {soft_min} (borderline)",
                    measured_value=rr,
                    threshold=soft_min,
                )
            )

    def _check_sl_distance(
        self,
        sig: CandidateSignal,
        failures: list[InvalidationFailure],
        checks: list[str],
        context,
    ) -> None:
        checks.append("sl_distance")
        entry_mid = (sig.entry_zone[0] + sig.entry_zone[1]) / 2

        # Centralised distance normalization (core/normalization.py)
        pip_size = self.cfg.get("pip_size", 0.01)
        if pip_size <= 0:
            pip_size = 0.01
        _dist = normalize_distance(entry_mid, sig.stop_loss, pip_size)
        sl_pts = _dist.points
        sl_min = self.cfg["sl_min_points"]
        sl_max = self.cfg["sl_max_points"]
        tol = self.cfg["sl_boundary_tolerance_pct"]

        if sl_pts < sl_min * (1 - tol) or sl_pts > sl_max * (1 + tol):
            # Safety-net diagnostic: if signal came through TradeConstructor,
            # this check should NOT fire — log a warning for investigation.
            _sl_source = getattr(sig, "sl_source", "")
            if _sl_source:
                logger.warning(
                    "safety-net triggered after construction: sl_distance "
                    "(sl_source=%s, sl_pts=%.1f, bounds=[%.0f, %.0f])",
                    _sl_source, sl_pts, sl_min, sl_max,
                )
            failures.append(
                InvalidationFailure(
                    "sl_distance",
                    "HARD_INVALIDATE",
                    f"SL distance {sl_pts:.1f} pts outside bounds [{sl_min}, {sl_max}]",
                    measured_value=sl_pts,
                )
            )
        elif sl_pts < sl_min or sl_pts > sl_max:
            _sl_source = getattr(sig, "sl_source", "")
            if _sl_source:
                logger.warning(
                    "safety-net triggered after construction: sl_distance "
                    "(sl_source=%s, sl_pts=%.1f, near boundary [%.0f, %.0f])",
                    _sl_source, sl_pts, sl_min, sl_max,
                )
            failures.append(
                InvalidationFailure(
                    "sl_distance",
                    "SOFT_INVALIDATE",
                    f"SL distance {sl_pts:.1f} pts near boundary [{sl_min}, {sl_max}]",
                    measured_value=sl_pts,
                )
            )

    def _check_confidence_floor(
        self,
        sig: CandidateSignal,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("confidence_floor")
        floor = self.cfg["confidence_hard_min"]
        if sig.raw_confidence < floor:
            failures.append(
                InvalidationFailure(
                    "confidence_floor",
                    "HARD_INVALIDATE",
                    f"Confidence {sig.raw_confidence:.2f} < floor {floor}",
                    measured_value=sig.raw_confidence,
                    threshold=floor,
                )
            )

    def _check_regime_setup(
        self,
        sig: CandidateSignal,
        regime: RegimeSnapshot,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("regime_setup")
        if regime.allowed_setups and sig.setup_type not in regime.allowed_setups:
            # This is a HOLD, not a REJECT — but we represent it as HARD
            # because the signal cannot proceed.  The orchestrator maps this
            # to CycleOutcome.HELD rather than REJECTED.
            failures.append(
                InvalidationFailure(
                    "regime_setup",
                    "HARD_INVALIDATE",
                    f"Setup '{sig.setup_type}' not allowed in '{regime.regime}' "
                    f"regime (allowed: {regime.allowed_setups})",
                )
            )

    # ------------------------------------------------------------------
    # Strategy-type-dependent checks
    # ------------------------------------------------------------------

    def _check_bos(
        self,
        sig: CandidateSignal,
        m15: dict,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("bos_required")
        bos = m15.get("bos", {})
        if not bos:
            failures.append(
                InvalidationFailure(
                    "bos_required",
                    "HARD_INVALIDATE",
                    "BOS data unavailable",
                )
            )
            return

        direction = sig.direction
        if direction == "BUY":
            has_bos = bos.get("bullish_bos", False)
            break_atr = float(bos.get("bullish_break_atr", 0) or 0)
        else:
            has_bos = bos.get("bearish_bos", False)
            break_atr = float(bos.get("bearish_break_atr", 0) or 0)

        weak_atr = self.cfg["bos_weak_atr"]

        if not has_bos:
            failures.append(
                InvalidationFailure(
                    "bos_required",
                    "HARD_INVALIDATE",
                    f"No {direction} BOS detected",
                )
            )
        elif break_atr < weak_atr:
            failures.append(
                InvalidationFailure(
                    "bos_required",
                    "SOFT_INVALIDATE",
                    f"Weak BOS: break {break_atr:.3f} ATR < {weak_atr} threshold",
                    measured_value=break_atr,
                    threshold=weak_atr,
                )
            )

    def _check_ema200(
        self,
        sig: CandidateSignal,
        m15: dict,
        context,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("ema200_alignment")
        ema200 = m15.get("ema200")
        if ema200 is None:
            # Fail-open if insufficient data
            return

        price = self._get_price(sig.direction, context)
        if price <= 0:
            return

        atr = float(m15.get("atr", 1.0) or 1.0)
        distance = price - ema200
        distance_atr = abs(distance) / atr if atr > 0 else 0

        wrong_side = (sig.direction == "BUY" and price < ema200) or (
            sig.direction == "SELL" and price > ema200
        )

        if wrong_side:
            hard_threshold = self.cfg["ema200_hard_atr"]
            soft_threshold = self.cfg["ema200_soft_atr"]

            if distance_atr > hard_threshold:
                failures.append(
                    InvalidationFailure(
                        "ema200_alignment",
                        "HARD_INVALIDATE",
                        f"Price {distance_atr:.2f} ATR on wrong side of EMA200",
                        measured_value=distance_atr,
                        threshold=hard_threshold,
                    )
                )
            elif distance_atr < soft_threshold:
                failures.append(
                    InvalidationFailure(
                        "ema200_alignment",
                        "SOFT_INVALIDATE",
                        f"Price marginally on wrong side of EMA200 ({distance_atr:.2f} ATR)",
                        measured_value=distance_atr,
                        threshold=soft_threshold,
                    )
                )
            else:
                failures.append(
                    InvalidationFailure(
                        "ema200_alignment",
                        "HARD_INVALIDATE",
                        f"Price {distance_atr:.2f} ATR on wrong side of EMA200",
                        measured_value=distance_atr,
                        threshold=hard_threshold,
                    )
                )

    def _check_swing(
        self,
        sig: CandidateSignal,
        m15: dict,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("swing_alignment")
        structure = str(m15.get("swing_structure", "")).lower()

        if not structure:
            return  # fail-open

        direction_lower = sig.direction.lower()

        # For pullback/continuation: structure must match direction
        if structure == "ranging":
            failures.append(
                InvalidationFailure(
                    "swing_alignment",
                    "SOFT_INVALIDATE",
                    f"Swing structure is 'ranging' (ambiguous for {sig.setup_type})",
                )
            )
        elif (direction_lower == "buy" and structure == "bearish") or (
            direction_lower == "sell" and structure == "bullish"
        ):
            failures.append(
                InvalidationFailure(
                    "swing_alignment",
                    "HARD_INVALIDATE",
                    f"Swing structure '{structure}' opposes {sig.direction} {sig.setup_type}",
                )
            )

    def _check_exhaustion(
        self,
        sig: CandidateSignal,
        m15: dict,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("exhaustion_required")
        ff = m15.get("fast_fingers", {})
        if not ff:
            failures.append(
                InvalidationFailure(
                    "exhaustion_required",
                    "HARD_INVALIDATE",
                    "Fast-fingers data unavailable for reversal setup",
                )
            )
            return

        exhaustion_score = float(ff.get("exhaustion_score", 0) or 0)

        # For reversal: need momentum exhaustion in the OPPOSITE direction
        if sig.direction == "BUY":
            is_exhausted = ff.get("is_exhausted_down", False)
        else:
            is_exhausted = ff.get("is_exhausted_up", False)

        if not is_exhausted:
            failures.append(
                InvalidationFailure(
                    "exhaustion_required",
                    "HARD_INVALIDATE",
                    f"No momentum exhaustion for reversal {sig.direction}",
                )
            )
        elif exhaustion_score < 30:
            failures.append(
                InvalidationFailure(
                    "exhaustion_required",
                    "SOFT_INVALIDATE",
                    f"Weak exhaustion (score={exhaustion_score:.0f} < 30)",
                    measured_value=exhaustion_score,
                    threshold=30.0,
                )
            )

    def _check_swing_ranging(
        self,
        m15: dict,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("swing_ranging")
        structure = str(m15.get("swing_structure", "")).lower()

        if not structure:
            return

        if structure != "ranging":
            failures.append(
                InvalidationFailure(
                    "swing_ranging",
                    "HARD_INVALIDATE",
                    f"Range-bounce requires ranging structure, got '{structure}'",
                )
            )

    def _check_bollinger(
        self,
        sig: CandidateSignal,
        m15: dict,
        failures: list[InvalidationFailure],
        checks: list[str],
    ) -> None:
        checks.append("bollinger_position")
        pct_b = m15.get("bb_pct_b")
        if pct_b is None:
            return  # fail-open

        pct_b = float(pct_b)
        hard = self.cfg["bb_hard_threshold"]
        mid_lo = self.cfg["bb_soft_mid_low"]
        mid_hi = self.cfg["bb_soft_mid_high"]

        if sig.direction == "BUY":
            # Range-bounce BUY should be near lower band (%B < 0.35)
            if pct_b > hard:
                failures.append(
                    InvalidationFailure(
                        "bollinger_position",
                        "HARD_INVALIDATE",
                        f"Range-bounce BUY but %B={pct_b:.2f} > {hard} (upper half)",
                        measured_value=pct_b,
                        threshold=hard,
                    )
                )
            elif mid_lo <= pct_b <= mid_hi:
                failures.append(
                    InvalidationFailure(
                        "bollinger_position",
                        "SOFT_INVALIDATE",
                        f"Range-bounce BUY with %B={pct_b:.2f} in mid-band zone",
                        measured_value=pct_b,
                    )
                )
        else:  # SELL
            # Range-bounce SELL should be near upper band (%B > 0.65)
            if pct_b < (1.0 - hard):
                failures.append(
                    InvalidationFailure(
                        "bollinger_position",
                        "HARD_INVALIDATE",
                        f"Range-bounce SELL but %B={pct_b:.2f} < {1-hard:.2f} (lower half)",
                        measured_value=pct_b,
                        threshold=1.0 - hard,
                    )
                )
            elif mid_lo <= pct_b <= mid_hi:
                failures.append(
                    InvalidationFailure(
                        "bollinger_position",
                        "SOFT_INVALIDATE",
                        f"Range-bounce SELL with %B={pct_b:.2f} in mid-band zone",
                        measured_value=pct_b,
                    )
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_price(direction: str, context) -> float:
        price = getattr(context, "price", None)
        if price is None:
            return 0.0
        if direction == "BUY":
            return float(getattr(price, "ask", 0) or 0)
        return float(getattr(price, "bid", 0) or 0)

    @staticmethod
    def _soft_penalty(f: InvalidationFailure) -> float:
        """Map soft failure to a penalty magnitude."""
        # Different checks carry different weight
        weights = {
            "rr_ratio": 30.0,
            "sl_distance": 15.0,
            "bos_required": 35.0,
            "ema200_alignment": 25.0,
            "swing_alignment": 20.0,
            "exhaustion_required": 30.0,
            "bollinger_position": 15.0,
        }
        return weights.get(f.check_name, 20.0)
