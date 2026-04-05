"""
pipeline/freshness.py — Signal timing decay calculator.

Measures how much a signal has degraded since generation based on
price distance from entry zone and elapsed candles.

All thresholds are initial calibration defaults.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from alphaloop.pipeline.types import CandidateSignal

logger = logging.getLogger(__name__)

# Calibration defaults
_DISTANCE_DECAY_START_ATR = 0.3   # start decaying beyond 0.3 ATR
_DISTANCE_REJECT_ATR = 0.8       # reject entirely beyond 0.8 ATR
_DECAY_PER_01_ATR = 0.10         # 10% per 0.1 ATR beyond threshold
_TIME_DECAY_START_CANDLES = 2    # start decaying after 2 candles
_TIME_REJECT_CANDLES = 5         # reject after 5 candles
_DECAY_PER_CANDLE = 0.05         # 5% per candle beyond threshold


def compute_freshness(
    signal: CandidateSignal,
    current_price: float,
    atr: float,
    candles_elapsed: int = 0,
    *,
    distance_decay_start: float = _DISTANCE_DECAY_START_ATR,
    distance_reject: float = _DISTANCE_REJECT_ATR,
    time_decay_start: int = _TIME_DECAY_START_CANDLES,
    time_reject: int = _TIME_REJECT_CANDLES,
) -> float:
    """
    Compute a freshness scalar (0.0 to 1.0) for a signal.

    Returns:
        1.0 = perfectly fresh
        0.0 = stale / too far from entry → should not execute
    """
    if atr <= 0:
        atr = 1.0  # prevent division by zero

    entry_center = (signal.entry_zone[0] + signal.entry_zone[1]) / 2
    distance_atr = abs(current_price - entry_center) / atr

    # Hard reject thresholds
    if distance_atr > distance_reject:
        logger.info(
            "[Freshness] REJECT: price moved %.2f ATR from entry (limit %.2f)",
            distance_atr,
            distance_reject,
        )
        return 0.0

    if candles_elapsed > time_reject:
        logger.info(
            "[Freshness] REJECT: %d candles elapsed (limit %d)",
            candles_elapsed,
            time_reject,
        )
        return 0.0

    # Distance decay
    price_decay = 0.0
    if distance_atr > distance_decay_start:
        excess = distance_atr - distance_decay_start
        price_decay = (excess / 0.1) * _DECAY_PER_01_ATR

    # Time decay
    time_decay = 0.0
    if candles_elapsed > time_decay_start:
        excess = candles_elapsed - time_decay_start
        time_decay = excess * _DECAY_PER_CANDLE

    freshness = max(0.0, 1.0 - price_decay - time_decay)

    if freshness < 1.0:
        logger.info(
            "[Freshness] scalar=%.3f (dist=%.2f ATR, candles=%d, "
            "price_decay=%.3f, time_decay=%.3f)",
            freshness,
            distance_atr,
            candles_elapsed,
            price_decay,
            time_decay,
        )

    return round(freshness, 4)
