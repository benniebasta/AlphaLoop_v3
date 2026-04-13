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
            "min_session_score": 0.20,   # Allow asia_early (0.20) — crypto never sleeps
        },
        # Crypto is volatile by nature — allow wider ATR bands
        "volatility_filter": {
            "max_atr_pct": 5.0,
            "min_atr_pct": 0.02,
        },
        # Wider SL/TP to absorb crypto micro-volatility
        "tick_jump_guard": {
            "max_tick_jump_atr": 1.5,
        },
        "liq_vacuum_guard": {
            "max_range_atr": 4.0,
            "min_body_pct": 20.0,
        },
        # More extension allowed — crypto can run far from VWAP
        "vwap_guard": {
            "max_extension_atr": 3.0,
        },
    },
    "spot_metal": {
        # Metals: standard session discipline, tighter spread
        "session_filter": {
            "min_session_score": 0.70,
        },
        "volatility_filter": {
            "max_atr_pct": 3.0,
            "min_atr_pct": 0.03,
        },
        # DXY alignment is more important for metals
        "dxy_filter": {
            "block_strength_threshold": 0.40,
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
        "vwap_guard": {
            "max_extension_atr": 1.5,
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
    },
    "index": {
        # Indices: strict session discipline (market hours only)
        "session_filter": {
            "min_session_score": 0.80,
        },
        "volatility_filter": {
            "max_atr_pct": 2.5,
            "min_atr_pct": 0.02,
        },
        "tick_jump_guard": {
            "max_tick_jump_atr": 1.2,
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
