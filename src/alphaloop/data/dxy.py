"""
data/dxy.py
Async DXY (US Dollar Index) data fetcher.

DXY has strong inverse correlation with gold:
  DXY rising  -> bearish gold pressure
  DXY falling -> bullish gold pressure

Data source: yfinance (DX-Y.NYB). Falls back to neutral on error.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Module-level cache
_cache: Optional[dict] = None
_cache_time: Optional[datetime] = None
_CACHE_TTL_MINUTES = 5


async def fetch_dxy_bias() -> dict:
    """
    Async fetch of DXY bias with strength, RSI, and directional recommendation.

    Returns dict with keys: bias, strength, current_level, change_1d_pct,
    change_1w_pct, rsi, recommendation, block_direction, source.
    """
    global _cache, _cache_time

    now = datetime.now(timezone.utc)
    if _cache and _cache_time and (now - _cache_time).total_seconds() < _CACHE_TTL_MINUTES * 60:
        return _cache

    result = await asyncio.to_thread(_fetch_and_analyze)
    _cache = result
    _cache_time = now
    return result


def _fetch_and_analyze() -> dict:
    """Sync DXY fetch via yfinance — run inside to_thread."""
    try:
        import yfinance as yf

        ticker = yf.Ticker("DX-Y.NYB")
        df = ticker.history(period="30d", interval="1d")

        if df is None or len(df) < 5:
            logger.warning("DXY data unavailable — using neutral bias")
            return _neutral()

        close = df["Close"]
        current = float(close.iloc[-1])
        prev_day = float(close.iloc[-2]) if len(close) >= 2 else current
        prev_week = float(close.iloc[-6]) if len(close) >= 6 else current

        change_1d = (current - prev_day) / prev_day * 100
        change_1w = (current - prev_week) / prev_week * 100

        # RSI
        delta = close.diff().dropna()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=13, adjust=False).mean()
        avg_loss = loss.ewm(com=13, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))
        rsi_val = float(rsi_series.iloc[-1])
        if math.isnan(rsi_val):
            rsi_val = 50.0

        # Bias determination
        strong_up = change_1d > 0.30 or change_1w > 0.80
        strong_down = change_1d < -0.30 or change_1w < -0.80

        if strong_up or rsi_val > 65:
            bias = "bullish_usd"
            strength = min(abs(change_1d) / 0.5 + abs(change_1w) / 1.0, 1.0)
            block_direction = "BUY"  # block gold buys when USD strong
        elif strong_down or rsi_val < 35:
            bias = "bearish_usd"
            strength = min(abs(change_1d) / 0.5 + abs(change_1w) / 1.0, 1.0)
            block_direction = "SELL"
        else:
            bias = "neutral"
            strength = 0.0
            block_direction = None

        result = {
            "bias": bias,
            "strength": round(strength, 3),
            "current_level": round(current, 3),
            "change_1d_pct": round(change_1d, 3),
            "change_1w_pct": round(change_1w, 3),
            "rsi": round(rsi_val, 1),
            "block_direction": block_direction,
            "source": "yfinance_dxy",
        }
        logger.info(
            f"DXY: {current:.2f} | 1d: {change_1d:+.2f}% | "
            f"1w: {change_1w:+.2f}% | RSI: {rsi_val:.1f} | Bias: {bias}"
        )
        return result

    except ImportError:
        logger.warning("yfinance not installed — DXY disabled")
        return _neutral()
    except Exception as e:
        logger.warning(f"DXY fetch failed: {e} — using neutral")
        return _neutral()


def _neutral() -> dict:
    return {
        "bias": "neutral",
        "strength": 0.0,
        "current_level": None,
        "change_1d_pct": 0.0,
        "change_1w_pct": 0.0,
        "rsi": 50.0,
        "block_direction": None,
        "source": "fallback_neutral",
    }
