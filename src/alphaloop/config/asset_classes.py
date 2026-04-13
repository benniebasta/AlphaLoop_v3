"""
Asset-class default overrides — Tier 1 in the parameter hierarchy.

Resolution order (highest wins):
  UI override > strategy.tools_config > asset symbol (AssetConfig)
  > asset class (this file) > global pipeline defaults

Use case: all crypto assets share relaxed session scoring, wider ATR bands,
and 24/7 trading rules without requiring per-symbol repetition.

Usage::

    from alphaloop.config.asset_classes import get_asset_class_defaults
    defaults = get_asset_class_defaults("crypto")
    min_score = defaults.get("min_session_score", 0.70)
"""

from __future__ import annotations

# Asset-class-level defaults for tool/pipeline parameters.
# Keys match tools_config param names so they can be merged into any
# tools_config dict before strategy-level overrides are applied.
_ASSET_CLASS_DEFAULTS: dict[str, dict[str, object]] = {
    "crypto": {
        # 24/7 markets — Asia sessions are valid trading windows
        "session_filter": {
            "min_session_score": 0.20,
        },
        "volatility_filter": {
            "max_atr_pct": 5.0,
            "min_atr_pct": 0.02,
        },
        "adx_filter": {
            "min_adx": 18.0,   # Crypto trends fast — slightly lower ADX bar
        },
        "fvg_guard": {
            "min_size_atr": 0.10,  # Smaller FVGs acceptable in fast crypto markets
        },
        "tick_jump_guard": {
            "max_tick_jump_atr": 1.5,
        },
        "liq_vacuum_guard": {
            "max_range_atr": 4.0,
            "min_body_pct": 20.0,
        },
        "vwap_guard": {
            "max_extension_atr": 3.0,
        },
        "rsi_feature": {
            "rsi_overbought": 75.0,
            "rsi_oversold": 25.0,
        },
        "bollinger_filter": {
            "buy_max_pct_b": 0.75,
            "sell_min_pct_b": 0.25,
        },
        "trendilo": {
            "strength_threshold": 25.0,
        },
        "choppiness_index": {
            "choppy_threshold": 65.0,
            "trending_threshold": 40.0,
        },
        "volume_filter": {
            "min_vol_ratio": 0.60,
        },
    },
    "spot_metal": {
        "session_filter": {
            "min_session_score": 0.70,
        },
        "volatility_filter": {
            "max_atr_pct": 3.0,
            "min_atr_pct": 0.03,
        },
        "adx_filter": {
            "min_adx": 20.0,
        },
        "fvg_guard": {
            "min_size_atr": 0.15,
        },
        "tick_jump_guard": {
            "max_tick_jump_atr": 0.9,
        },
        "liq_vacuum_guard": {
            "max_range_atr": 3.0,
            "min_body_pct": 25.0,
        },
        "vwap_guard": {
            "max_extension_atr": 2.0,
        },
        "dxy_filter": {
            "block_strength_threshold": 0.40,
        },
        "rsi_feature": {
            "rsi_overbought": 70.0,
            "rsi_oversold": 30.0,
        },
        "bollinger_filter": {
            "buy_max_pct_b": 0.70,
            "sell_min_pct_b": 0.30,
        },
        "trendilo": {
            "strength_threshold": 25.0,
        },
        "choppiness_index": {
            "choppy_threshold": 61.8,
            "trending_threshold": 38.2,
        },
        "volume_filter": {
            "min_vol_ratio": 0.70,
        },
    },
    "forex_major": {
        "session_filter": {
            "min_session_score": 0.70,
        },
        "volatility_filter": {
            "max_atr_pct": 1.5,
            "min_atr_pct": 0.01,
        },
        "adx_filter": {
            "min_adx": 22.0,   # Forex needs stronger trend confirmation
        },
        "fvg_guard": {
            "min_size_atr": 0.12,
        },
        "tick_jump_guard": {
            "max_tick_jump_atr": 0.7,
        },
        "liq_vacuum_guard": {
            "max_range_atr": 2.5,
            "min_body_pct": 30.0,
        },
        "vwap_guard": {
            "max_extension_atr": 1.5,
        },
        "rsi_feature": {
            "rsi_overbought": 70.0,
            "rsi_oversold": 30.0,
        },
        "bollinger_filter": {
            "buy_max_pct_b": 0.65,
            "sell_min_pct_b": 0.35,
        },
        "trendilo": {
            "strength_threshold": 30.0,
        },
        "choppiness_index": {
            "choppy_threshold": 61.8,
            "trending_threshold": 38.2,
        },
        "volume_filter": {
            "min_vol_ratio": 0.75,
        },
    },
    "forex_minor": {
        "session_filter": {
            "min_session_score": 0.75,
        },
        "volatility_filter": {
            "max_atr_pct": 2.0,
            "min_atr_pct": 0.01,
        },
        "adx_filter": {
            "min_adx": 22.0,
        },
        "fvg_guard": {
            "min_size_atr": 0.12,
        },
        "tick_jump_guard": {
            "max_tick_jump_atr": 0.8,
        },
        "liq_vacuum_guard": {
            "max_range_atr": 2.5,
            "min_body_pct": 30.0,
        },
        "vwap_guard": {
            "max_extension_atr": 1.5,
        },
        "rsi_feature": {
            "rsi_overbought": 70.0,
            "rsi_oversold": 30.0,
        },
        "bollinger_filter": {
            "buy_max_pct_b": 0.68,
            "sell_min_pct_b": 0.32,
        },
        "trendilo": {
            "strength_threshold": 30.0,
        },
        "choppiness_index": {
            "choppy_threshold": 62.0,
            "trending_threshold": 38.0,
        },
        "volume_filter": {
            "min_vol_ratio": 0.70,
        },
    },
    "index": {
        "session_filter": {
            "min_session_score": 0.80,
        },
        "volatility_filter": {
            "max_atr_pct": 2.5,
            "min_atr_pct": 0.02,
        },
        "adx_filter": {
            "min_adx": 22.0,
        },
        "fvg_guard": {
            "min_size_atr": 0.15,
        },
        "tick_jump_guard": {
            "max_tick_jump_atr": 1.2,
        },
        "liq_vacuum_guard": {
            "max_range_atr": 3.0,
            "min_body_pct": 25.0,
        },
        "vwap_guard": {
            "max_extension_atr": 2.0,
        },
        "rsi_feature": {
            "rsi_overbought": 72.0,
            "rsi_oversold": 28.0,
        },
        "bollinger_filter": {
            "buy_max_pct_b": 0.70,
            "sell_min_pct_b": 0.30,
        },
        "trendilo": {
            "strength_threshold": 25.0,
        },
        "choppiness_index": {
            "choppy_threshold": 60.0,
            "trending_threshold": 38.2,
        },
        "volume_filter": {
            "min_vol_ratio": 0.80,
        },
    },
}


def get_asset_class_defaults(asset_class: str) -> dict[str, dict[str, object]]:
    """Return tool parameter defaults for the given asset class.

    Returns a nested dict keyed by plugin name → param dict, matching
    the tools_config schema. Returns an empty dict for unknown asset classes.

    Example::

        defaults = get_asset_class_defaults("crypto")
        # {"session_filter": {"min_session_score": 0.20}, "volatility_filter": {...}, ...}
    """
    return _ASSET_CLASS_DEFAULTS.get(asset_class, {})


def merge_tools_config(
    asset_class: str,
    strategy_tools_config: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """Merge asset-class defaults with strategy-level tools_config.

    Strategy-level values win over asset-class defaults.
    Returns a new dict — does not mutate inputs.

    Usage (in _build_tools_config)::

        merged = merge_tools_config(asset_cfg.asset_class, strategy_tc)
        # merged = asset_class_defaults | strategy_tools_config (deep merge)
    """
    class_defaults = get_asset_class_defaults(asset_class)
    result: dict[str, dict[str, object]] = {}

    # Start with asset-class defaults
    for plugin, params in class_defaults.items():
        result[plugin] = dict(params)

    # Strategy-level overrides win (deep merge per plugin)
    for plugin, params in strategy_tools_config.items():
        if plugin in result:
            result[plugin] = {**result[plugin], **params}
        else:
            result[plugin] = dict(params)

    return result
