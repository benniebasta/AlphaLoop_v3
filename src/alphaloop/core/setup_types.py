"""Canonical setup-type normalization for pipeline/runtime consumers."""

from __future__ import annotations


_ALIASES: dict[str, str] = {
    "pullback": "pullback",
    "breakout": "breakout",
    "reversal": "reversal",
    "continuation": "continuation",
    "range_bounce": "range_bounce",
    "range": "range_bounce",
    "momentum": "continuation",
    "trend": "continuation",
    "trend_continuation": "continuation",
    "pullback_continuation": "pullback",
    "range_reversal": "reversal",
    "breakout_retest": "breakout",
    "momentum_expansion": "continuation",
    "discretionary_ai": "pullback",
}


def normalize_pipeline_setup_type(raw: str | None, *, default: str = "pullback") -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    return _ALIASES.get(value, value)


_SCHEMA_ALIASES: dict[str, str] = {
    "pullback": "pullback",
    "breakout": "breakout",
    "reversal": "reversal",
    "range": "range",
    "momentum": "momentum",
    "scalp": "scalp",
    "continuation": "pullback",
    "range_bounce": "range",
    "trend": "momentum",
    "trend_continuation": "pullback",
    "pullback_continuation": "pullback",
    "range_reversal": "reversal",
    "breakout_retest": "breakout",
    "momentum_expansion": "momentum",
    "discretionary_ai": "pullback",
}


def normalize_schema_setup_type(raw: str | None, *, default: str = "pullback") -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    return _SCHEMA_ALIASES.get(value, default)
