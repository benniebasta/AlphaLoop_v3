"""
seedlab/stability.py — Cross-regime stability analysis.

Evaluates whether a seed performs consistently across different
market regimes, detecting regime-specific weaknesses.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from alphaloop.seedlab.metrics import SeedMetrics, compute_regime_consistency

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_MIN_SHARPE = 0.3
DEFAULT_MIN_WIN_RATE = 0.35
DEFAULT_MAX_DRAWDOWN_PCT = -25.0
DEFAULT_MIN_REGIME_CONSISTENCY = 0.3
DEFAULT_MIN_PROFIT_FACTOR = 1.0


class StabilityReport(BaseModel):
    """Result of cross-regime stability analysis."""

    passed: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)

    # Regime analysis
    regime_consistency: float = 0.0
    worst_regime: str = ""
    worst_sharpe: float | None = None
    best_regime: str = ""
    best_sharpe: float | None = None

    # Aggregate checks
    full_sharpe_ok: bool = False
    full_wr_ok: bool = False
    full_dd_ok: bool = False
    full_pf_ok: bool = False
    regime_consistency_ok: bool = False

    model_config = {"frozen": True}


def analyze_stability(
    full_metrics: SeedMetrics,
    regime_metrics: dict[str, SeedMetrics],
    thresholds: dict[str, Any] | None = None,
) -> StabilityReport:
    """
    Analyze whether a seed meets stability requirements across regimes.

    Args:
        full_metrics: Metrics from full-data backtest.
        regime_metrics: Per-regime metrics.
        thresholds: Optional overrides for stability thresholds.

    Returns:
        StabilityReport with pass/fail and detailed reasons.
    """
    t = thresholds or {}
    min_sharpe = t.get("min_sharpe", DEFAULT_MIN_SHARPE)
    min_wr = t.get("min_win_rate", DEFAULT_MIN_WIN_RATE)
    max_dd = t.get("max_drawdown_pct", DEFAULT_MAX_DRAWDOWN_PCT)
    min_consistency = t.get("min_regime_consistency", DEFAULT_MIN_REGIME_CONSISTENCY)
    min_pf = t.get("min_profit_factor", DEFAULT_MIN_PROFIT_FACTOR)

    reasons: list[str] = []

    # Full-data checks
    full_sharpe_ok = full_metrics.sharpe is not None and full_metrics.sharpe >= min_sharpe
    if not full_sharpe_ok:
        reasons.append(
            f"Sharpe {full_metrics.sharpe} below minimum {min_sharpe}"
        )

    full_wr_ok = full_metrics.win_rate >= min_wr
    if not full_wr_ok:
        reasons.append(
            f"Win rate {full_metrics.win_rate:.1%} below minimum {min_wr:.1%}"
        )

    full_dd_ok = full_metrics.max_drawdown_pct >= max_dd
    if not full_dd_ok:
        reasons.append(
            f"Max drawdown {full_metrics.max_drawdown_pct:.1f}% exceeds limit {max_dd:.1f}%"
        )

    full_pf_ok = full_metrics.profit_factor >= min_pf
    if not full_pf_ok:
        reasons.append(
            f"Profit factor {full_metrics.profit_factor:.2f} below minimum {min_pf:.2f}"
        )

    # Regime consistency
    consistency = compute_regime_consistency(regime_metrics)
    regime_consistency_ok = consistency >= min_consistency or len(regime_metrics) < 2
    if not regime_consistency_ok:
        reasons.append(
            f"Regime consistency {consistency:.2f} below minimum {min_consistency:.2f}"
        )

    # Find worst/best regimes
    worst_regime = ""
    worst_sharpe: float | None = None
    best_regime = ""
    best_sharpe: float | None = None

    for name, rm in regime_metrics.items():
        if rm.sharpe is None:
            continue
        if worst_sharpe is None or rm.sharpe < worst_sharpe:
            worst_sharpe = rm.sharpe
            worst_regime = name
        if best_sharpe is None or rm.sharpe > best_sharpe:
            best_sharpe = rm.sharpe
            best_regime = name

    passed = len(reasons) == 0

    report = StabilityReport(
        passed=passed,
        rejection_reasons=reasons,
        regime_consistency=consistency,
        worst_regime=worst_regime,
        worst_sharpe=worst_sharpe,
        best_regime=best_regime,
        best_sharpe=best_sharpe,
        full_sharpe_ok=full_sharpe_ok,
        full_wr_ok=full_wr_ok,
        full_dd_ok=full_dd_ok,
        full_pf_ok=full_pf_ok,
        regime_consistency_ok=regime_consistency_ok,
    )

    if passed:
        logger.info("Stability PASSED (Sharpe=%.2f, consistency=%.2f)",
                     full_metrics.sharpe or 0, consistency)
    else:
        logger.info("Stability FAILED: %s", "; ".join(reasons))

    return report
