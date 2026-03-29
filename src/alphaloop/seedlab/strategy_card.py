"""
seedlab/strategy_card.py — Immutable output artifact.

A StrategyCard is the final output of the SeedLab pipeline:
- Immutable Pydantic model of a validated strategy configuration
- Contains filters, params, metrics, regime support, risk profile
- Serializable to JSON for storage in registry
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from alphaloop.seedlab.metrics import SeedMetrics
from alphaloop.seedlab.ranking import SeedScore
from alphaloop.seedlab.seed_generator import compute_seed_hash
from alphaloop.seedlab.stability import StabilityReport

logger = logging.getLogger(__name__)


class StrategyCard(BaseModel):
    """
    Complete strategy card — the immutable output artifact of seed evaluation.
    """

    # Identity
    name: str
    seed_hash: str
    symbol: str
    category: str

    # Filter configuration
    filters: list[str] = Field(default_factory=list)

    # Parameters used in backtest
    params: dict[str, Any] = Field(default_factory=dict)

    # Metrics (from full-data backtest)
    metrics: dict[str, Any] = Field(default_factory=dict)

    # Regime support
    regimes_supported: list[str] = Field(default_factory=list)
    regime_metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Risk profile
    risk_profile: dict[str, Any] = Field(default_factory=dict)

    # Scores
    confidence_score: float = 0.0
    total_score: float = 0.0
    score_breakdown: dict[str, float] = Field(default_factory=dict)

    # Stability
    stability_passed: bool = False
    stability_reasons: list[str] = Field(default_factory=list)

    # Metadata
    created_at: str = ""
    backtest_bars: int = 0
    backtest_days: int = 0

    # Status
    status: str = "candidate"  # candidate / approved / rejected / promoted

    model_config = {"frozen": True}

    def model_post_init(self, __context: Any) -> None:
        if not self.created_at:
            object.__setattr__(
                self, "created_at", datetime.now(timezone.utc).isoformat()
            )


def build_strategy_card(
    name: str,
    symbol: str,
    category: str,
    filters: list[str],
    params: dict[str, Any],
    full_metrics: SeedMetrics,
    regime_metrics: dict[str, SeedMetrics],
    stability: StabilityReport,
    score: SeedScore,
    backtest_bars: int = 0,
    backtest_days: int = 0,
) -> StrategyCard:
    """
    Construct a StrategyCard from all evaluation artifacts.
    """
    metrics_dict = {
        "trade_count": full_metrics.trade_count,
        "win_rate": round(full_metrics.win_rate, 4),
        "profit_factor": round(min(full_metrics.profit_factor, 99.0), 3),
        "sharpe": full_metrics.sharpe,
        "sortino": full_metrics.sortino,
        "max_drawdown_pct": full_metrics.max_drawdown_pct,
        "max_dd_duration": full_metrics.max_dd_duration,
        "avg_rr": round(full_metrics.avg_rr, 3),
        "expectancy": round(full_metrics.expectancy, 2),
        "total_pnl": round(full_metrics.total_pnl, 2),
        "equity_slope": full_metrics.equity_slope,
        "equity_r2": full_metrics.equity_r2,
        "variance_stability": full_metrics.variance_stability,
    }

    # Regime support: regimes with positive Sharpe and sufficient trades
    regimes_supported: list[str] = []
    regime_dict: dict[str, dict[str, Any]] = {}
    for rname, rm in regime_metrics.items():
        regime_dict[rname] = {
            "trade_count": rm.trade_count,
            "win_rate": round(rm.win_rate, 4),
            "sharpe": rm.sharpe,
            "max_drawdown_pct": rm.max_drawdown_pct,
            "profit_factor": round(min(rm.profit_factor, 99.0), 3),
        }
        if rm.sharpe is not None and rm.sharpe > 0 and rm.trade_count >= 10:
            regimes_supported.append(rname)

    risk_profile = {
        "max_drawdown_pct": full_metrics.max_drawdown_pct,
        "worst_regime": stability.worst_regime,
        "worst_regime_sharpe": stability.worst_sharpe,
        "regime_consistency": stability.regime_consistency,
    }

    score_breakdown = {
        "sharpe": score.sharpe_score,
        "stability": score.stability_score,
        "profit_factor": score.pf_score,
        "low_dd": score.dd_score,
        "trade_count": score.trade_count_score,
        "regime": score.regime_score,
        "equity": score.equity_score,
    }

    card = StrategyCard(
        name=name,
        seed_hash=compute_seed_hash(sorted(filters)),
        symbol=symbol,
        category=category,
        filters=sorted(filters),
        params=params,
        metrics=metrics_dict,
        regimes_supported=sorted(regimes_supported),
        regime_metrics=regime_dict,
        risk_profile=risk_profile,
        confidence_score=round(score.total_score, 4),
        total_score=round(score.total_score, 4),
        score_breakdown=score_breakdown,
        stability_passed=stability.passed,
        stability_reasons=stability.rejection_reasons,
        backtest_bars=backtest_bars,
        backtest_days=backtest_days,
        status="candidate" if stability.passed else "rejected",
    )

    logger.info(
        "Built card %r: score=%.4f, status=%s, filters=%s",
        card.name, card.total_score, card.status, card.filters,
    )
    return card
