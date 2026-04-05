"""
research/monte_carlo.py — Monte Carlo simulation for strategy robustness testing.

Shuffles trade P&L sequences to test whether observed performance is
statistically significant or could arise by chance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SIMULATIONS = 5_000
SIGNIFICANCE_LEVEL = 0.05


class MonteCarloSimulator:
    """
    Monte Carlo significance and robustness testing for trade sequences.

    Tests:
    1. Significance: Is the observed Sharpe better than random reshuffling?
    2. Drawdown distribution: What is the expected worst-case drawdown?
    3. Ruin probability: What fraction of random paths hit a ruin threshold?
    """

    def __init__(self, n_simulations: int = DEFAULT_SIMULATIONS, seed: int = 42) -> None:
        self.n_simulations = n_simulations
        self._rng = np.random.default_rng(seed)

    async def run_significance_test(
        self, pnl_values: list[float]
    ) -> dict[str, Any]:
        """
        Permutation test: shuffle PnL order many times and compare
        the observed Sharpe to the distribution of shuffled Sharpes.

        Returns dict with p_value, is_significant, observed/mean/percentiles.
        """
        if len(pnl_values) < 20:
            return {
                "status": "insufficient_data",
                "trade_count": len(pnl_values),
            }

        arr = np.array(pnl_values, dtype=np.float64)

        # Validate input — reject NaN/inf values
        if not np.all(np.isfinite(arr)):
            non_finite = (~np.isfinite(arr)).sum()
            logger.warning("[monte-carlo] %d non-finite values in PnL array — filtering", non_finite)
            arr = arr[np.isfinite(arr)]
            if len(arr) < 20:
                return {"status": "insufficient_data", "trade_count": len(arr)}

        observed_sharpe = self._sharpe(arr)
        if observed_sharpe is None:
            return {"status": "sharpe_undefined"}

        # Permutation test — offload CPU work to a thread
        shuffled_sharpes = await asyncio.to_thread(
            self._significance_loop, arr
        )

        if not shuffled_sharpes:
            return {"status": "simulation_failed"}

        dist = np.array(shuffled_sharpes)
        p_value = float(np.mean(dist >= observed_sharpe))
        is_significant = p_value < SIGNIFICANCE_LEVEL

        if is_significant:
            recommendation = (
                f"Strategy Sharpe ({observed_sharpe:.2f}) is statistically significant "
                f"(p={p_value:.3f}). Edge is unlikely due to chance."
            )
        else:
            recommendation = (
                f"Strategy Sharpe ({observed_sharpe:.2f}) is NOT significant "
                f"(p={p_value:.3f}). Observed results could arise from random ordering."
            )

        return {
            "status": "complete",
            "trade_count": len(pnl_values),
            "observed_sharpe": round(observed_sharpe, 3),
            "p_value": round(p_value, 4),
            "is_significant": is_significant,
            "shuffled_mean": round(float(np.mean(dist)), 3),
            "shuffled_p5": round(float(np.percentile(dist, 5)), 3),
            "shuffled_p95": round(float(np.percentile(dist, 95)), 3),
            "recommendation": recommendation,
        }

    async def run_drawdown_analysis(
        self,
        pnl_values: list[float],
        initial_balance: float = 10_000.0,
    ) -> dict[str, Any]:
        """
        Simulate equity paths by reshuffling trades and compute
        the distribution of max drawdowns.
        """
        if len(pnl_values) < 10:
            return {"status": "insufficient_data"}

        arr = np.array(pnl_values, dtype=np.float64)

        # Offload CPU work to a thread
        dd_arr = await asyncio.to_thread(
            self._drawdown_loop, arr, initial_balance
        )
        return {
            "status": "complete",
            "median_max_dd_pct": round(float(np.median(dd_arr)), 2),
            "p5_max_dd_pct": round(float(np.percentile(dd_arr, 5)), 2),
            "p95_max_dd_pct": round(float(np.percentile(dd_arr, 95)), 2),
            "worst_max_dd_pct": round(float(dd_arr.min()), 2),
        }

    async def run_ruin_probability(
        self,
        pnl_values: list[float],
        initial_balance: float = 10_000.0,
        ruin_threshold_pct: float = 50.0,
    ) -> dict[str, Any]:
        """
        Estimate the probability that a random ordering of trades
        would draw the account below the ruin threshold.
        """
        if len(pnl_values) < 10:
            return {"status": "insufficient_data"}

        arr = np.array(pnl_values, dtype=np.float64)
        # Clamp ruin threshold to valid range
        ruin_threshold_pct = max(0.0, min(100.0, ruin_threshold_pct))
        ruin_level = initial_balance * (1 - ruin_threshold_pct / 100)

        # Offload CPU work to a thread
        ruin_count = await asyncio.to_thread(
            self._ruin_loop, arr, initial_balance, ruin_level
        )

        probability = ruin_count / self.n_simulations
        return {
            "status": "complete",
            "ruin_threshold_pct": ruin_threshold_pct,
            "ruin_probability": round(probability, 4),
            "ruin_count": ruin_count,
            "total_simulations": self.n_simulations,
        }

    def _significance_loop(self, arr: np.ndarray) -> list[float]:
        """Sync helper: run permutation Sharpe simulations."""
        shuffled_sharpes: list[float] = []
        for _ in range(self.n_simulations):
            shuffled = self._rng.permutation(arr)
            s = self._sharpe(shuffled)
            if s is not None:
                shuffled_sharpes.append(s)
        return shuffled_sharpes

    def _drawdown_loop(self, arr: np.ndarray, initial_balance: float) -> np.ndarray:
        """Sync helper: run drawdown simulations."""
        max_dds: list[float] = []
        for _ in range(self.n_simulations):
            shuffled = self._rng.permutation(arr)
            equity = initial_balance + np.cumsum(shuffled)
            peak = np.maximum.accumulate(equity)
            dd_pct = ((equity - peak) / peak) * 100
            max_dds.append(float(dd_pct.min()))
        return np.array(max_dds)

    def _ruin_loop(
        self, arr: np.ndarray, initial_balance: float, ruin_level: float
    ) -> int:
        """Sync helper: count ruin events."""
        ruin_count = 0
        for _ in range(self.n_simulations):
            shuffled = self._rng.permutation(arr)
            equity = initial_balance + np.cumsum(shuffled)
            if equity.min() <= ruin_level:
                ruin_count += 1
        return ruin_count

    @staticmethod
    def _sharpe(arr: np.ndarray, annualization: float = 252.0) -> float | None:
        """Compute Sharpe ratio from PnL array."""
        if len(arr) < 10:
            return None
        std = float(np.std(arr, ddof=1))
        if std == 0:
            return None
        return float(np.mean(arr) / std * np.sqrt(annualization))
