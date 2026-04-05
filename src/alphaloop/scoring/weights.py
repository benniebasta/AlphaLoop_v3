"""
scoring/weights.py
Default and configurable group weights for the confidence engine.

Weights must sum to 1.0. If loaded from strategy JSON and they don't,
they are normalized automatically.
"""

from __future__ import annotations

SCORING_GROUPS = ("trend", "momentum", "structure", "volume", "volatility")

DEFAULT_GROUP_WEIGHTS: dict[str, float] = {
    "trend":      0.30,
    "momentum":   0.25,
    "structure":  0.20,
    "volume":     0.10,
    "volatility": 0.15,
}

DEFAULT_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "strong_entry": 75.0,   # >= this -> full size
    "min_entry":    60.0,   # >= this -> reduced size
    # < min_entry -> HOLD (no trade)
}


def load_weights(strategy_params: dict | None = None) -> dict[str, float]:
    """
    Load group weights from strategy params, falling back to defaults.

    Normalizes weights to sum to 1.0 if they don't already.
    Unknown groups are ignored; missing groups get 0.0.
    """
    if not strategy_params:
        return dict(DEFAULT_GROUP_WEIGHTS)

    raw = strategy_params.get("scoring_weights")
    if not raw or not isinstance(raw, dict):
        return dict(DEFAULT_GROUP_WEIGHTS)

    # Filter to known groups only
    weights = {}
    for group in SCORING_GROUPS:
        try:
            val = float(raw.get(group, DEFAULT_GROUP_WEIGHTS.get(group, 0)))
        except (TypeError, ValueError):
            val = DEFAULT_GROUP_WEIGHTS.get(group, 0)
        weights[group] = max(0.0, val)

    # Normalize to sum to 1.0
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}
    else:
        return dict(DEFAULT_GROUP_WEIGHTS)

    return weights


def load_thresholds(strategy_params: dict | None = None) -> dict[str, float]:
    """Load confidence thresholds from strategy params, falling back to defaults."""
    if not strategy_params:
        return dict(DEFAULT_CONFIDENCE_THRESHOLDS)

    raw = strategy_params.get("confidence_thresholds")
    if not raw or not isinstance(raw, dict):
        return dict(DEFAULT_CONFIDENCE_THRESHOLDS)

    thresholds = dict(DEFAULT_CONFIDENCE_THRESHOLDS)
    for key in ("strong_entry", "min_entry"):
        if key in raw:
            try:
                val = float(raw[key])
                thresholds[key] = max(0.0, min(100.0, val))
            except (TypeError, ValueError):
                pass

    # Enforce ordering invariant: strong_entry must be >= min_entry
    if thresholds["strong_entry"] < thresholds["min_entry"]:
        thresholds["strong_entry"] = thresholds["min_entry"]

    return thresholds
