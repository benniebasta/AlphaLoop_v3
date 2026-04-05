"""
execution/tca.py — Transaction Cost Analysis.

Computes execution quality metrics from closed trade history:
  - Average slippage in points/pips
  - Slippage vs ATR ratio
  - Spread cost in USD
  - Execution quality score (0–100)

Usage:
    analyzer = TCAAnalyzer(trades)
    report = analyzer.compute()
    # {'avg_slippage_points': 0.8, 'execution_quality_score': 87, ...}
"""

from __future__ import annotations

import logging
import statistics
from typing import Any

logger = logging.getLogger(__name__)

# Score thresholds: score = 100 when avg_slippage < PERFECT_PIPS; 0 when >= MAX_PIPS
_PERFECT_SLIPPAGE_PIPS = 0.5
_MAX_SLIPPAGE_PIPS = 5.0


class TCAAnalyzer:
    """
    Transaction Cost Analyzer for closed trade history.

    Parameters
    ----------
    trades : list[dict]
        Each trade dict needs: slippage_points, execution_spread,
        lot_size, pnl_usd, and optionally atr_h1.
    pip_value_per_lot : float
        USD per pip per lot for the asset. Default 10.0 (XAUUSD ≈ $10/pip/lot).
    """

    def __init__(
        self,
        trades: list[dict],
        pip_value_per_lot: float = 10.0,
    ) -> None:
        self._trades = trades
        self._pip_value = pip_value_per_lot

    def compute(self) -> dict[str, Any]:
        """
        Compute TCA metrics from trade history.

        Returns
        -------
        dict with keys:
            trade_count, avg_slippage_points, max_slippage_points,
            avg_spread_cost_usd, total_spread_cost_usd,
            slippage_vs_atr_pct, execution_quality_score (0-100),
            score_label (str: Excellent / Good / Fair / Poor)
        """
        if not self._trades:
            return self._empty_report()

        slippages: list[float] = []
        spread_costs: list[float] = []
        atr_ratios: list[float] = []

        for t in self._trades:
            slip = float(t.get("slippage_points") or 0.0)
            spread = float(t.get("execution_spread") or 0.0)
            lots = float(t.get("lot_size") or 0.0)
            atr = float(t.get("atr_h1") or 0.0)

            slippages.append(slip)

            # Spread cost = half-spread (enter only) × pip_value × lots
            if lots > 0:
                spread_costs.append(spread * 0.5 * lots * self._pip_value)

            # Slippage as % of ATR
            if atr > 0:
                atr_ratios.append(slip / atr * 100)

        avg_slip = statistics.mean(slippages) if slippages else 0.0
        max_slip = max(slippages) if slippages else 0.0
        avg_spread_cost = statistics.mean(spread_costs) if spread_costs else 0.0
        total_spread_cost = sum(spread_costs)
        avg_atr_ratio = statistics.mean(atr_ratios) if atr_ratios else 0.0

        # Execution quality score: 100 at perfect_pips, 0 at max_pips (linear)
        score = max(
            0.0,
            min(
                100.0,
                ((_MAX_SLIPPAGE_PIPS - avg_slip) / (_MAX_SLIPPAGE_PIPS - _PERFECT_SLIPPAGE_PIPS)) * 100,
            ),
        )
        score = round(score, 1)

        if score >= 80:
            label = "Excellent"
        elif score >= 60:
            label = "Good"
        elif score >= 40:
            label = "Fair"
        else:
            label = "Poor"

        return {
            "trade_count": len(self._trades),
            "avg_slippage_points": round(avg_slip, 4),
            "max_slippage_points": round(max_slip, 4),
            "avg_spread_cost_usd": round(avg_spread_cost, 2),
            "total_spread_cost_usd": round(total_spread_cost, 2),
            "slippage_vs_atr_pct": round(avg_atr_ratio, 2),
            "execution_quality_score": score,
            "score_label": label,
        }

    def _empty_report(self) -> dict[str, Any]:
        return {
            "trade_count": 0,
            "avg_slippage_points": 0.0,
            "max_slippage_points": 0.0,
            "avg_spread_cost_usd": 0.0,
            "total_spread_cost_usd": 0.0,
            "slippage_vs_atr_pct": 0.0,
            "execution_quality_score": 100.0,
            "score_label": "No data",
        }
