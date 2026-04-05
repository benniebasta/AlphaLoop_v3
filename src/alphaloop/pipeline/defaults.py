"""
pipeline/defaults.py — Centralised calibration defaults for the v4 pipeline.

Every numeric threshold used across the pipeline is defined here as an
initial calibration value.  All values are overridable via strategy config
under the ``pipeline_v4`` key.

These are STARTING POINTS, not permanent constants.  Phase 4 calibration
should tune them against historical data before live deployment.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Stage 1: MarketGate
# ---------------------------------------------------------------------------
MARKET_GATE = {
    "stale_bar_seconds": 300,   # 5 min for M15 bars
    "min_bars_required": 200,   # need 200 for EMA200
    "spread_ratio_max": 3.0,    # 3x rolling median
    # weekend / news blackout / dead-market checks are owned by
    # session_filter / news_filter / volatility_filter plugins respectively.
}

# ---------------------------------------------------------------------------
# Stage 2: RegimeClassifier
# ---------------------------------------------------------------------------
REGIME = {
    "choppiness_ranging_threshold": 61.8,
    "adx_ranging_threshold": 15.0,
    "choppiness_trending_threshold": 38.2,
    "adx_trending_threshold": 25.0,
    "atr_pct_volatile": 0.007,          # 0.7%
    "vol_compressed_max": 0.0015,       # 0.15%
    "vol_normal_max": 0.005,            # 0.50%
    "vol_elevated_max": 0.01,           # 1.0%
}

# ---------------------------------------------------------------------------
# Stage 4A: StructuralInvalidation
# ---------------------------------------------------------------------------
INVALIDATION = {
    "rr_hard_min": 1.0,                 # R:R below this = HARD_INVALIDATE
    "rr_soft_min": 1.5,                 # R:R below this = SOFT_INVALIDATE
    "confidence_hard_min": 0.30,        # Signal generator confidence floor
    "sl_min_points": 20.0,
    "sl_max_points": 300.0,
    "sl_boundary_tolerance_pct": 0.10,  # within 10% of boundary = SOFT
    "bos_weak_atr": 0.2,               # BOS break < this = weak
    "ema200_hard_atr": 1.0,            # > this ATR on wrong side = HARD
    "ema200_soft_atr": 0.3,            # < this ATR on wrong side = SOFT
    "bb_hard_threshold": 0.65,          # range_bounce %B invalidation
    "bb_soft_mid_low": 0.45,
    "bb_soft_mid_high": 0.55,
}

# ---------------------------------------------------------------------------
# Stage 5: ConvictionScorer
# ---------------------------------------------------------------------------
CONVICTION = {
    "max_total_conviction_penalty": 50.0,  # Penalty budget cap
    "conflict_spread_threshold": 40.0,     # No penalty below this spread
    "conflict_penalty_rate": 0.75,         # Points per spread unit above threshold
    "conflict_penalty_cap": 30.0,
    "portfolio_macro_penalty": 8.0,
    "portfolio_budget_low_threshold": 0.03,
    "portfolio_budget_max_penalty": 20.0,
    "portfolio_penalty_cap": 25.0,
    "quality_floor_overall": 35.0,
    "quality_floor_contradiction_count": 3,
    "quality_floor_contradiction_threshold": 25.0,
    "quality_floor_max_score_min": 60.0,
    "quality_floor_win_rate_min": 0.40,
}

# ---------------------------------------------------------------------------
# Stage 6: AI Validator (algo_ai)
# ---------------------------------------------------------------------------
AI_VALIDATOR = {
    "entry_adj_max_atr": 0.3,
    "sl_adj_max_atr": 0.5,
    "tp_adj_max_atr": 0.5,
    "confidence_boost_max": 0.05,
}

# ---------------------------------------------------------------------------
# Stage 8: ExecutionGuard + Freshness
# ---------------------------------------------------------------------------
EXECUTION = {
    "tick_jump_atr_max": 0.8,
    "liq_vacuum_spike_mult": 2.5,
    "liq_vacuum_body_pct": 30.0,
    "max_delay_candles": 3,
    "freshness_distance_decay_start_atr": 0.3,
    "freshness_distance_reject_atr": 0.8,
    "freshness_time_decay_start_candles": 2,
    "freshness_time_reject_candles": 5,
}

# ---------------------------------------------------------------------------
# ECE / Adaptive AI weight (ai_signal mode)
# ---------------------------------------------------------------------------
ECE = {
    "ece_well_calibrated": 0.05,
    "ece_reasonable": 0.10,
    "ece_drifting": 0.15,
    "ai_weight_base": 0.50,
    "ai_weight_max": 0.60,
    "ai_weight_min": 0.25,
    "ai_weight_hysteresis": 0.03,       # minimum ECE change to adjust weight
}


# ---------------------------------------------------------------------------
# Convenience: flatten all into one dict for strategy JSON merging
# ---------------------------------------------------------------------------
def get_all_defaults() -> dict[str, dict]:
    """Return all pipeline defaults grouped by stage."""
    return {
        "market_gate": dict(MARKET_GATE),
        "regime": dict(REGIME),
        "invalidation": dict(INVALIDATION),
        "conviction": dict(CONVICTION),
        "ai_validator": dict(AI_VALIDATOR),
        "execution": dict(EXECUTION),
        "ece": dict(ECE),
    }


def load_pipeline_config(
    strategy_validation: dict | None = None,
) -> dict[str, dict]:
    """
    Merge strategy-level pipeline_v4 overrides on top of defaults.

    Usage:
        cfg = load_pipeline_config(strategy.validation)
        invalidator = StructuralInvalidator(cfg=cfg["invalidation"])
    """
    defaults = get_all_defaults()

    if not strategy_validation:
        return defaults

    overrides = strategy_validation.get("pipeline_v4", {})
    if not overrides or not isinstance(overrides, dict):
        return defaults

    for stage_key, stage_defaults in defaults.items():
        stage_overrides = overrides.get(stage_key, {})
        if isinstance(stage_overrides, dict):
            stage_defaults.update(stage_overrides)

    return defaults
