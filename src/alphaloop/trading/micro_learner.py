"""
Micro-learning — lightweight per-trade parameter nudges.

After each TradeClosed event, applies small adjustments to runtime params
without full Optuna retraining. Caps at ±1% per trade, ±5% total drift.

Changes stored in DB as micro_adjustments_{symbol} and merged on strategy load.
Reset when full autolearn runs.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MicroAdjustment:
    """A single parameter nudge."""
    param: str
    direction: str  # "up" or "down"
    amount: float
    reason: str


class MicroLearner:
    """
    Per-trade parameter nudges with strict guardrails.

    Adjustments:
      1. Confidence recal: if high-conf trades losing, nudge min_confidence up
      2. SL distance: if SL hit rate too high, nudge sl_atr_mult up
      3. Validation: if rejection rate too high for a rule, relax threshold

    Guardrails:
      - Each adjustment capped at ±1% of current value per trade
      - Total drift from baseline capped at ±5%
      - Stored in DB, survives restarts
      - Reset on full autolearn cycle
    """

    def __init__(
        self,
        max_per_trade_pct: float = 0.01,
        max_total_drift_pct: float = 0.05,
        sl_lookback: int = 10,
        conf_lookback: int = 20,
    ):
        self._max_per_trade = max_per_trade_pct
        self._max_drift = max_total_drift_pct
        self._sl_lookback = sl_lookback
        self._conf_lookback = conf_lookback

        # Rolling trade history
        self._sl_hits: deque[bool] = deque(maxlen=sl_lookback)
        self._conf_outcomes: deque[tuple[float, bool]] = deque(maxlen=conf_lookback)

        # Cumulative adjustments from baseline
        self._adjustments: dict[str, float] = {}
        self._baseline_params: dict[str, float] = {}

    def set_baseline(self, params: dict) -> None:
        """Set the baseline params (from strategy JSON). Called on strategy load."""
        self._baseline_params = {k: float(v) for k, v in params.items() if isinstance(v, (int, float))}
        # Don't reset adjustments here — they're loaded from DB

    def load_adjustments(self, adjustments: dict[str, float]) -> None:
        """Load persisted adjustments from DB."""
        self._adjustments = dict(adjustments)

    def get_adjustments(self) -> dict[str, float]:
        """Get current adjustments for DB persistence."""
        return dict(self._adjustments)

    def get_adjusted_params(self, base_params: dict) -> dict:
        """Apply micro-adjustments to base params and return modified copy."""
        result = dict(base_params)
        for param, delta in self._adjustments.items():
            if param in result:
                try:
                    result[param] = float(result[param]) + delta
                except (TypeError, ValueError):
                    pass
        return result

    def on_trade_closed(
        self,
        pnl: float,
        sl_hit: bool,
        confidence: float,
        current_params: dict,
    ) -> list[MicroAdjustment] | None:
        """
        Process a closed trade and return adjustments (if any).

        Returns list of MicroAdjustment or None if no changes needed.
        """
        self._sl_hits.append(sl_hit)
        self._conf_outcomes.append((confidence, pnl > 0))

        adjustments = []

        # 1. SL distance — if hit rate too high, widen stops
        if len(self._sl_hits) >= self._sl_lookback:
            sl_hit_rate = sum(self._sl_hits) / len(self._sl_hits)
            if sl_hit_rate > 0.70:
                adj = self._nudge("sl_atr_mult", "up", current_params)
                if adj:
                    adjustments.append(adj)

        # 2. Confidence recalibration — if high-conf trades losing
        if len(self._conf_outcomes) >= 10:
            high_conf = [(c, w) for c, w in self._conf_outcomes if c > 0.8]
            if len(high_conf) >= 5:
                high_conf_wr = sum(1 for _, w in high_conf if w) / len(high_conf)
                if high_conf_wr < 0.50:
                    adj = self._nudge("min_confidence", "up", current_params, amount_override=0.01)
                    if adj:
                        adjustments.append(adj)

        return adjustments if adjustments else None

    def _nudge(
        self,
        param: str,
        direction: str,
        current_params: dict,
        amount_override: float | None = None,
    ) -> MicroAdjustment | None:
        """Apply a single parameter nudge with guardrails."""
        current_val = current_params.get(param)
        if current_val is None:
            return None

        try:
            current_val = float(current_val)
        except (TypeError, ValueError):
            return None

        if current_val == 0:
            return None

        # Compute nudge amount (±1% of current value)
        amount = amount_override or abs(current_val * self._max_per_trade)
        if direction == "down":
            amount = -amount

        # Check total drift cap
        baseline = self._baseline_params.get(param, current_val)
        current_drift = self._adjustments.get(param, 0.0)
        new_drift = current_drift + amount
        max_allowed = abs(baseline * self._max_drift)

        if abs(new_drift) > max_allowed:
            logger.debug(
                "[micro-learn] Drift cap reached for %s: %.4f > ±%.4f",
                param, new_drift, max_allowed,
            )
            return None

        self._adjustments[param] = new_drift
        logger.info(
            "[micro-learn] Nudged %s %s by %.4f (total drift: %.4f)",
            param, direction, amount, new_drift,
        )

        return MicroAdjustment(
            param=param,
            direction=direction,
            amount=amount,
            reason=f"{'SL hit rate >70%' if param == 'sl_atr_mult' else 'High-conf WR <50%'}",
        )

    def reset(self) -> None:
        """Reset all adjustments. Called when full autolearn completes."""
        self._adjustments.clear()
        self._sl_hits.clear()
        self._conf_outcomes.clear()
        logger.info("[micro-learn] Reset — full autolearn cycle completed")
