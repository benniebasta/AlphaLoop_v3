"""
Live strategy parameters — asset-agnostic container for tunable values.
Defaults are seeded from AssetConfig at startup.
The auto-trainer / AutoLearn mutates this at runtime.
"""

from __future__ import annotations

import logging
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Bounds for validate_strategy_params()
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "rsi_overbought": (60.0, 90.0),
    "rsi_oversold": (10.0, 40.0),
    "rsi_extreme_ob": (65.0, 95.0),
    "rsi_extreme_os": (5.0, 35.0),
    "min_confidence": (0.3, 1.0),
    "min_rr": (0.5, 10.0),
    "tp1_rr": (1.0, 5.0),
    "tp2_rr": (1.5, 10.0),
    "sl_atr_mult": (0.8, 5.0),
    "min_session_score": (0.3, 1.0),
    "max_volatility_atr_mult": (1.0, 5.0),
}


class StrategyParams(BaseModel):
    """Tunable strategy parameters — mutated by AutoLearn at runtime."""

    # Structure
    structure_lookback_candles: int = 50
    significant_level_min_points: float = 150.0
    sweep_wick_min_points: float = 80.0
    sweep_close_inside_pct: float = 0.60

    # Entry
    entry_zone_atr_mult: float = 0.25
    limit_order_offset_points: float = 20.0

    # Stop Loss
    sl_atr_mult: float = 1.5
    sl_min_points: float = 150.0
    sl_max_points: float = 500.0

    # Take Profit
    tp1_rr: float = 1.5
    tp1_close_pct: float = 0.60
    tp2_rr: float = 2.5
    trail_start_rr: float = 1.0

    # Indicators
    ema_fast: int = 21
    ema_slow: int = 55
    ema_trend: int = 200
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    rsi_extreme_ob: float = 75.0
    rsi_extreme_os: float = 25.0
    atr_period: int = 14

    # Session filters
    session_weights: dict[str, float] = Field(default_factory=lambda: {
        "london_ny_overlap": 1.0,
        "ny_session": 0.85,
        "london_session": 0.80,
        "asia_late": 0.40,
        "asia_early": 0.20,
        "weekend": 0.0,
    })
    min_session_score: float = 0.70

    # Correlation guards
    dxy_conflict_threshold_pct: float = 0.30
    dxy_conflict_size_reduction: float = 0.50

    # Volatility
    max_volatility_atr_mult: float = 2.5
    max_spread_mult: float = 2.0

    # Asset pip size (seeded from AssetConfig)
    pip_size: float = 0.1

    # Anti-overfit guardrails
    min_trades_for_tuning: int = 30
    max_param_change_pct: float = 0.15


def validate_strategy_params(params: dict) -> dict:
    """Clamp strategy JSON thresholds to safe bounds. Returns the clamped dict."""
    p = params.get("params", params)
    for key, (lo, hi) in _PARAM_BOUNDS.items():
        if key in p:
            try:
                val = float(p[key])
                clamped = max(lo, min(hi, val))
                if clamped != val:
                    logger.warning(
                        f"[strategy-validate] {key}={val} clamped to [{lo},{hi}] -> {clamped}"
                    )
                    p[key] = clamped
            except (TypeError, ValueError):
                pass
    return params
