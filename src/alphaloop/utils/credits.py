"""AI credit/token usage tracking."""

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# {provider: {model: {"tokens_in": int, "tokens_out": int, "calls": int}}}
_usage: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"tokens_in": 0, "tokens_out": 0, "calls": 0}))
_start_time = time.time()


def record_usage(
    provider: str,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Record token usage for a model call."""
    _usage[provider][model]["tokens_in"] += tokens_in
    _usage[provider][model]["tokens_out"] += tokens_out
    _usage[provider][model]["calls"] += 1


def get_usage_summary() -> dict:
    """Return accumulated usage statistics."""
    result = {}
    for provider, models in _usage.items():
        result[provider] = {}
        for model, stats in models.items():
            result[provider][model] = dict(stats)
    return result


def get_session_duration_hours() -> float:
    return (time.time() - _start_time) / 3600


def reset() -> None:
    """Reset all usage counters."""
    global _start_time
    _usage.clear()
    _start_time = time.time()
