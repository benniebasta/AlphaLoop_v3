"""
Strategy health monitor — composite score for MetaLoop decision-making.

health_score = w1×sharpe + w2×winrate - w3×drawdown - w4×stagnation

Thresholds:
  > 0.6  → Healthy (no action)
  0.3–0.6 → Degrading (trigger retrain)
  < 0.3  → Critical (immediate rollback)
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADING = "degrading"
    CRITICAL = "critical"


@dataclass
class HealthResult:
    score: float
    status: HealthStatus
    sharpe_component: float
    winrate_component: float
    drawdown_component: float
    stagnation_component: float


class StrategyHealthMonitor:
    """
    Composite health scorer for active strategy performance.

    Fed by TradeClosed events. Evaluates rolling metrics over a window.
    """

    def __init__(
        self,
        window: int = 30,
        w_sharpe: float = 0.35,
        w_winrate: float = 0.25,
        w_drawdown: float = 0.25,
        w_stagnation: float = 0.15,
        healthy_threshold: float = 0.6,
        critical_threshold: float = 0.3,
    ):
        self._window = window
        self._w_sharpe = w_sharpe
        self._w_winrate = w_winrate
        self._w_drawdown = w_drawdown
        self._w_stagnation = w_stagnation
        self._healthy = healthy_threshold
        self._critical = critical_threshold

        self._pnl_history: deque[float] = deque(maxlen=window)
        self._cumulative_pnl: float = 0.0
        self._peak_pnl: float = 0.0
        self._trades_since_peak: int = 0

    def record(self, pnl: float) -> None:
        """Record a closed trade's PnL."""
        self._pnl_history.append(pnl)
        self._cumulative_pnl += pnl

        if self._cumulative_pnl > self._peak_pnl:
            self._peak_pnl = self._cumulative_pnl
            self._trades_since_peak = 0
        else:
            self._trades_since_peak += 1

    def evaluate(self) -> HealthResult:
        """Compute composite health score from rolling metrics."""
        n = len(self._pnl_history)
        if n < 5:
            return HealthResult(
                score=0.7, status=HealthStatus.HEALTHY,
                sharpe_component=0, winrate_component=0,
                drawdown_component=0, stagnation_component=0,
            )

        pnl = list(self._pnl_history)

        # Sharpe (normalized to 0–1 via sigmoid-like mapping)
        import numpy as np
        arr = np.array(pnl)
        raw_sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0
        sharpe_norm = min(max((raw_sharpe + 1) / 3, 0), 1)  # maps [-1,2] → [0,1]

        # Win rate
        wins = sum(1 for p in pnl if p > 0)
        winrate = wins / n

        # Drawdown (current DD depth as fraction of peak)
        dd_depth = (self._peak_pnl - self._cumulative_pnl) if self._peak_pnl > 0 else 0
        dd_frac = min(dd_depth / max(self._peak_pnl, 1), 1.0)

        # Stagnation (trades since last new high / window)
        stagnation = min(self._trades_since_peak / max(self._window, 1), 1.0)

        score = (
            self._w_sharpe * sharpe_norm
            + self._w_winrate * winrate
            - self._w_drawdown * dd_frac
            - self._w_stagnation * stagnation
        )
        score = max(0, min(1, score))

        if score > self._healthy:
            status = HealthStatus.HEALTHY
        elif score > self._critical:
            status = HealthStatus.DEGRADING
        else:
            status = HealthStatus.CRITICAL

        return HealthResult(
            score=round(score, 3),
            status=status,
            sharpe_component=round(sharpe_norm, 3),
            winrate_component=round(winrate, 3),
            drawdown_component=round(dd_frac, 3),
            stagnation_component=round(stagnation, 3),
        )

    def reset(self) -> None:
        """Reset all state (called after strategy version change)."""
        self._pnl_history.clear()
        self._cumulative_pnl = 0.0
        self._peak_pnl = 0.0
        self._trades_since_peak = 0
