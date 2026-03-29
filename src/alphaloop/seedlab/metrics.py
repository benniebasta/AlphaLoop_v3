"""
seedlab/metrics.py — Pydantic metrics model for seed evaluation.

Extracts institutional-grade metrics from backtest results:
win_rate, profit_factor, sharpe, sortino, max_drawdown, equity slope, etc.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pydantic import BaseModel, Field


class SeedMetrics(BaseModel):
    """Complete metrics for one seed in one regime (or full data)."""

    # Identification
    seed_hash: str = ""
    regime: str = "full"

    # Core metrics
    trade_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    avg_rr: float = 0.0
    expectancy: float = 0.0

    # Risk metrics
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown_pct: float = 0.0
    max_dd_duration: int = 0

    # Stability metrics
    equity_slope: float = 0.0
    equity_r2: float = 0.0
    variance_stability: float = 0.0

    def is_valid(self, min_trades: int = 10) -> bool:
        """Minimum viability check."""
        return self.trade_count >= min_trades and self.sharpe is not None

    model_config = {"frozen": True}


def extract_metrics(
    pnl_usd: list[float],
    pnl_r: list[float],
    outcomes: list[str],
    equity_curve: list[float],
    seed_hash: str = "",
    regime: str = "full",
    annualization_factor: float = 252.0,
) -> SeedMetrics:
    """
    Extract full metrics from backtest trade data.

    Args:
        pnl_usd: List of P&L values in USD per closed trade.
        pnl_r: List of P&L in R-multiples per closed trade.
        outcomes: List of outcome strings ("WIN", "LOSS", "BE").
        equity_curve: Balance values after each trade close.
        seed_hash: Identifier for the seed.
        regime: Regime label.
        annualization_factor: Trading days per year.

    Returns:
        SeedMetrics with all fields populated.
    """
    trade_count = len(pnl_usd)
    if trade_count == 0:
        return SeedMetrics(seed_hash=seed_hash, regime=regime)

    wins_pnl = [p for p in pnl_usd if p > 0]
    losses_pnl = [p for p in pnl_usd if p < 0]

    win_rate = sum(1 for o in outcomes if o == "WIN") / len(outcomes) if outcomes else 0.0
    total_pnl = sum(pnl_usd)

    gross_profit = sum(wins_pnl) if wins_pnl else 0.0
    gross_loss = abs(sum(losses_pnl)) if losses_pnl else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0
    )

    avg_rr = sum(pnl_r) / len(pnl_r) if pnl_r else 0.0
    expectancy = total_pnl / trade_count if trade_count > 0 else 0.0

    # Sharpe
    sharpe = None
    arr = np.array(pnl_usd, dtype=np.float64)
    if len(arr) >= 10:
        std = float(np.std(arr, ddof=1))
        if std > 0:
            sharpe = round(float(np.mean(arr) / std * math.sqrt(annualization_factor)), 3)

    # Sortino
    sortino = None
    downside = arr[arr < 0]
    if len(downside) >= 3:
        ds_std = float(np.std(downside, ddof=1))
        if ds_std > 0:
            sortino = round(float(np.mean(arr) / ds_std * math.sqrt(annualization_factor)), 3)

    # Max drawdown
    max_dd_pct = 0.0
    max_dd_duration = 0
    if len(equity_curve) >= 2:
        eq = np.array(equity_curve, dtype=np.float64)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / np.where(peak > 0, peak, 1.0)
        max_dd_pct = round(float(dd.min()) * 100, 2)

        # DD duration
        in_dd = (eq < peak).astype(int)
        if in_dd.any():
            changes = np.diff(in_dd, prepend=0)
            groups = np.cumsum(np.abs(changes))
            for g in np.unique(groups):
                mask = groups == g
                if in_dd[mask].sum() > 0:
                    max_dd_duration = max(max_dd_duration, int(in_dd[mask].sum()))

    # Equity slope + R-squared
    equity_slope = 0.0
    equity_r2 = 0.0
    if len(equity_curve) >= 5:
        eq = np.array(equity_curve, dtype=np.float64)
        if eq[0] > 0:
            eq_norm = eq / eq[0]
        else:
            eq_norm = eq
        x = np.arange(len(eq_norm), dtype=np.float64)
        try:
            coeffs = np.polyfit(x, eq_norm, 1)
            equity_slope = round(float(coeffs[0]), 6)
            y_pred = np.polyval(coeffs, x)
            ss_res = float(np.sum((eq_norm - y_pred) ** 2))
            ss_tot = float(np.sum((eq_norm - eq_norm.mean()) ** 2))
            equity_r2 = round(1.0 - (ss_res / ss_tot), 4) if ss_tot > 0 else 0.0
        except Exception:
            pass

    # Variance stability
    variance_stability = 0.0
    if len(pnl_usd) >= 20:
        rolling = np.convolve(arr, np.ones(10) / 10, mode="valid")
        if len(rolling) >= 3:
            rm_std = float(np.std(rolling))
            rm_mean = float(np.mean(rolling))
            cv = abs(rm_std / rm_mean) if rm_mean != 0 else float("inf")
            variance_stability = round(max(0.0, 1.0 - cv), 4)

    return SeedMetrics(
        seed_hash=seed_hash,
        regime=regime,
        trade_count=trade_count,
        win_rate=round(win_rate, 4),
        profit_factor=round(min(pf, 9999.99), 3),
        total_pnl=round(total_pnl, 2),
        avg_rr=round(avg_rr, 3),
        expectancy=round(expectancy, 2),
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd_pct,
        max_dd_duration=max_dd_duration,
        equity_slope=equity_slope,
        equity_r2=equity_r2,
        variance_stability=variance_stability,
    )


def compute_regime_consistency(regime_metrics: dict[str, SeedMetrics]) -> float:
    """
    Score how consistent a seed performs across regimes.

    Uses coefficient of variation of Sharpe ratios.
    Returns: 0.0 (wildly inconsistent) to 1.0 (perfectly consistent).
    """
    sharpes = [
        m.sharpe for m in regime_metrics.values()
        if m.sharpe is not None and m.trade_count >= 10
    ]
    if len(sharpes) < 2:
        return 0.0

    mean_s = float(np.mean(sharpes))
    std_s = float(np.std(sharpes))
    if mean_s <= 0:
        return 0.0

    cv = std_s / mean_s
    return round(max(0.0, min(1.0, 1.0 - cv)), 4)
