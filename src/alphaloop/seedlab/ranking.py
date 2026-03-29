"""
seedlab/ranking.py — Composite scoring for seed ranking.

Scores seeds on multiple dimensions and produces a total composite
score for ranking and comparison.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from alphaloop.seedlab.metrics import SeedMetrics
from alphaloop.seedlab.stability import StabilityReport

logger = logging.getLogger(__name__)

# Scoring weights (sum to 1.0)
WEIGHTS = {
    "sharpe": 0.25,
    "stability": 0.15,
    "profit_factor": 0.15,
    "low_dd": 0.15,
    "trade_count": 0.10,
    "regime": 0.10,
    "equity": 0.10,
}


class SeedScore(BaseModel):
    """Composite score for a single seed."""

    seed_hash: str = ""
    seed_name: str = ""

    # Component scores (0.0 to 1.0 each)
    sharpe_score: float = 0.0
    stability_score: float = 0.0
    pf_score: float = 0.0
    dd_score: float = 0.0
    trade_count_score: float = 0.0
    regime_score: float = 0.0
    equity_score: float = 0.0

    # Weighted total
    total_score: float = 0.0

    model_config = {"frozen": True}


def score_seed(
    metrics: SeedMetrics,
    stability: StabilityReport,
    seed_name: str = "",
) -> SeedScore:
    """
    Compute composite score for a seed.

    Each dimension is normalized to 0.0-1.0, then weighted.
    """
    # Sharpe: 0 -> 0.0, 2.0+ -> 1.0
    sharpe_raw = max(0.0, metrics.sharpe or 0.0)
    sharpe_score = min(1.0, sharpe_raw / 2.0)

    # Stability: regime consistency (already 0-1)
    stability_score = stability.regime_consistency

    # Profit factor: 1.0 -> 0.0, 3.0+ -> 1.0
    pf_raw = max(0.0, min(metrics.profit_factor, 10.0))
    pf_score = min(1.0, max(0.0, (pf_raw - 1.0) / 2.0))

    # Low drawdown: 0% -> 1.0, -30%+ -> 0.0
    dd_raw = abs(metrics.max_drawdown_pct)
    dd_score = max(0.0, min(1.0, 1.0 - dd_raw / 30.0))

    # Trade count: 0 -> 0.0, 100+ -> 1.0
    tc_score = min(1.0, metrics.trade_count / 100.0)

    # Regime score: bonus for passing stability
    regime_score = 1.0 if stability.passed else 0.3

    # Equity curve quality: R-squared (already 0-1)
    equity_score = max(0.0, metrics.equity_r2)

    # Weighted total
    total = (
        WEIGHTS["sharpe"] * sharpe_score
        + WEIGHTS["stability"] * stability_score
        + WEIGHTS["profit_factor"] * pf_score
        + WEIGHTS["low_dd"] * dd_score
        + WEIGHTS["trade_count"] * tc_score
        + WEIGHTS["regime"] * regime_score
        + WEIGHTS["equity"] * equity_score
    )

    return SeedScore(
        seed_hash=metrics.seed_hash,
        seed_name=seed_name,
        sharpe_score=round(sharpe_score, 4),
        stability_score=round(stability_score, 4),
        pf_score=round(pf_score, 4),
        dd_score=round(dd_score, 4),
        trade_count_score=round(tc_score, 4),
        regime_score=round(regime_score, 4),
        equity_score=round(equity_score, 4),
        total_score=round(total, 4),
    )


def rank_seeds(scores: list[SeedScore]) -> list[SeedScore]:
    """Sort seeds by total_score descending."""
    return sorted(scores, key=lambda s: s.total_score, reverse=True)
