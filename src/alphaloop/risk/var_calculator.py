"""
risk/var_calculator.py — Historical VaR and CVaR calculator.

Computes 95th/99th percentile loss estimates from a PnL series.
Used by RiskMonitor to provide probabilistic risk context alongside
the rule-based daily-loss and consecutive-loss checks.

Usage:
    calc = HistoricalVaRCalculator(confidence_level=0.95)
    calc.fit(pnl_list)           # list of daily/per-trade PnL values
    var_value  = calc.var()      # e.g. -250.0  (loss at 95th pct)
    cvar_value = calc.cvar()     # e.g. -380.0  (mean of worst 5%)
    breached   = calc.var_breach(-300.0)  # True if loss exceeds VaR
"""

from __future__ import annotations

import logging
import statistics

logger = logging.getLogger(__name__)


class HistoricalVaRCalculator:
    """
    Historical simulation VaR and CVaR calculator.

    Fits on a PnL series and exposes percentile-based risk estimates.
    No external dependencies required — uses stdlib `statistics` only.

    Parameters
    ----------
    confidence_level : float
        VaR confidence level, e.g. 0.95 for 95% VaR. Default 0.95.
    lookback_days : int
        Max number of observations to fit on (FIFO window). Default 252.
    """

    def __init__(
        self,
        confidence_level: float = 0.95,
        lookback_days: int = 252,
    ) -> None:
        if not (0 < confidence_level < 1):
            raise ValueError(f"confidence_level must be in (0, 1), got {confidence_level}")
        self._confidence = confidence_level
        self._lookback = lookback_days
        self._sorted_losses: list[float] = []   # sorted ascending (most negative first)
        self._fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, pnl_series: list[float]) -> None:
        """
        Fit the calculator on a PnL series.

        Parameters
        ----------
        pnl_series : list[float]
            Per-trade or per-day PnL values. Positive = profit, negative = loss.
            Windowed to `lookback_days` most-recent entries.
        """
        if not pnl_series:
            logger.warning("[var] fit() called with empty series — VaR unavailable")
            self._sorted_losses = []
            self._fitted = False
            return

        windowed = pnl_series[-self._lookback:]
        self._sorted_losses = sorted(windowed)
        self._fitted = True
        logger.debug(
            "[var] fitted on %d observations | VaR(%.0f%%)=%.2f | CVaR=%.2f",
            len(windowed),
            self._confidence * 100,
            self.var() or 0,
            self.cvar() or 0,
        )

    def var(self, confidence: float | None = None) -> float | None:
        """
        Return VaR at the given confidence level.

        Returns the PnL value at the (1 - confidence) percentile of the
        distribution, i.e. the loss that is exceeded only (1-confidence)
        of the time.

        Returns None if fewer than 5 observations are available.
        """
        if not self._fitted or len(self._sorted_losses) < 5:
            return None
        c = confidence or self._confidence
        loss_tail = 1.0 - c
        idx = max(0, int(len(self._sorted_losses) * loss_tail) - 1)
        return round(self._sorted_losses[idx], 2)

    def var99(self) -> float | None:
        """Convenience: 99% VaR."""
        return self.var(confidence=0.99)

    def cvar(self, confidence: float | None = None) -> float | None:
        """
        Return CVaR (Expected Shortfall) — mean of losses beyond VaR.

        Returns None if insufficient data.
        """
        if not self._fitted or len(self._sorted_losses) < 5:
            return None
        c = confidence or self._confidence
        loss_tail = 1.0 - c
        cutoff = max(1, int(len(self._sorted_losses) * loss_tail))
        tail = self._sorted_losses[:cutoff]
        if not tail:
            return None
        return round(statistics.mean(tail), 2)

    def var_breach(self, new_pnl: float) -> bool:
        """
        Return True if new_pnl is worse than the current VaR threshold.

        Advisory check — does NOT modify state.
        """
        threshold = self.var()
        if threshold is None:
            return False
        return new_pnl < threshold

    def is_fitted(self) -> bool:
        """Return True if the calculator has been fitted on data."""
        return self._fitted and len(self._sorted_losses) >= 5

    @property
    def observation_count(self) -> int:
        return len(self._sorted_losses)

    def summary(self) -> dict:
        """Return a JSON-serializable summary dict."""
        return {
            "fitted": self._fitted,
            "observations": self.observation_count,
            "confidence_level": self._confidence,
            "var_95": self.var(0.95),
            "var_99": self.var(0.99),
            "cvar_95": self.cvar(0.95),
            "cvar_99": self.cvar(0.99),
        }
