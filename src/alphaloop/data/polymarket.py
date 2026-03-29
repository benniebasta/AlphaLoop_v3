"""
data/polymarket.py
Async sentiment fetch from Polymarket prediction markets.

Uses the Gamma API to search for active macro markets and synthesises
a directional bias for trading. Falls back to neutral on error.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_URL = "https://clob.polymarket.com/markets"

# Search keywords -> gold/macro sentiment interpretation
# (keyword, higher_prob_is_bullish_gold, weight)
MARKET_KEYWORDS = [
    ("federal reserve rate cut", True, 0.40),
    ("fed rate cut", True, 0.40),
    ("us recession", True, 0.30),
    ("dollar index", False, 0.25),
    ("gold price", True, 0.35),
    ("inflation", True, 0.20),
]

# Module-level cache
_cache: Optional[dict] = None
_cache_time: Optional[datetime] = None
_CACHE_TTL_MINUTES = 60


async def fetch_sentiment() -> dict:
    """
    Async fetch of Polymarket macro sentiment.

    Returns dict with keys: bias, confidence, raw_score, signals,
    markets_found, source.
    """
    global _cache, _cache_time

    now = datetime.now(timezone.utc)
    if _cache and _cache_time and (now - _cache_time).total_seconds() < _CACHE_TTL_MINUTES * 60:
        return _cache

    result = await _fetch_and_compute()
    _cache = result
    _cache_time = now
    return result


async def _fetch_and_compute() -> dict:
    """Search Polymarket for active macro markets and compute bias."""
    signals: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=8.0) as client:
        for keyword, bullish_gold, weight in MARKET_KEYWORDS:
            prob = await _search_best_probability(client, keyword)
            if prob is not None:
                key = keyword.replace(" ", "_")[:30]
                signals[key] = {"prob": prob, "bullish_gold": bullish_gold, "weight": weight}

    bias, confidence, score = _compute_bias(signals)

    result = {
        "bias": bias,
        "confidence": confidence,
        "raw_score": score,
        "signals": {k: v["prob"] for k, v in signals.items()},
        "markets_found": len(signals),
        "source": "polymarket",
    }

    if signals:
        logger.info(
            f"Polymarket: {len(signals)} active markets | "
            f"Bias: {bias} (score {score:+.2f}, conf {confidence:.2f})"
        )
    else:
        logger.debug("Polymarket: no active markets found — using neutral")

    return result


async def _search_best_probability(
    client: httpx.AsyncClient, keyword: str
) -> Optional[float]:
    """Search Gamma API for active markets matching keyword."""
    try:
        resp = await client.get(
            GAMMA_URL,
            params={"search": keyword, "active": "true", "closed": "false", "limit": 5},
        )
        if resp.status_code != 200:
            return None
        markets = resp.json()
        if not markets:
            return None

        best = max(markets, key=lambda m: float(m.get("volume", 0) or 0))
        outcomes = best.get("outcomePrices")
        if outcomes:
            try:
                prices = [float(p) for p in outcomes]
                return prices[0]  # index 0 = YES
            except Exception:
                pass

        cond_id = best.get("conditionId")
        if cond_id:
            return await _clob_yes_price(client, cond_id)
    except Exception as e:
        logger.debug(f"Polymarket search failed for '{keyword}': {e}")
    return None


async def _clob_yes_price(
    client: httpx.AsyncClient, condition_id: str
) -> Optional[float]:
    """Get YES token price from CLOB."""
    try:
        resp = await client.get(f"{CLOB_URL}/{condition_id}")
        if resp.status_code != 200:
            return None
        data = resp.json()
        for token in data.get("tokens", []):
            if str(token.get("outcome", "")).lower() == "yes":
                return float(token.get("price", 0.5))
    except Exception:
        pass
    return None


def _compute_bias(signals: dict) -> tuple[str, float, float]:
    """Compute weighted bias from collected signals."""
    if not signals:
        return "neutral", 0.5, 0.0

    score = 0.0
    weight_sum = 0.0

    for info in signals.values():
        prob = info["prob"]
        w = info["weight"]
        directional = (prob - 0.5) * 2
        if not info["bullish_gold"]:
            directional = -directional
        score += directional * w
        weight_sum += w

    if weight_sum == 0:
        return "neutral", 0.5, 0.0

    norm = score / weight_sum
    if norm > 0.20:
        bias = "bullish"
    elif norm < -0.20:
        bias = "bearish"
    else:
        bias = "neutral"

    confidence = min(0.5 + abs(norm) * 0.5, 0.95)
    return bias, round(confidence, 3), round(norm, 3)
