"""
data/news.py
Async economic calendar fetcher (FMP API).

Returns upcoming HIGH/CRITICAL impact events for the news filter.
Falls back to a blocking sentinel when the API key is missing or
the API is unreachable — trades are protected by default.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FMP_BASE = "https://financialmodelingprep.com/api/v3/economic_calendar"

# Module-level cache
_cache_ts: Optional[datetime] = None
_cache_events: list[dict] = []
_CACHE_TTL_SECONDS = 300


async def fetch_upcoming_news(
    lookahead_hours: int = 4,
    api_key: str | None = None,
) -> list[dict]:
    """
    Async fetch of upcoming economic events from FMP.

    Returns list of dicts: {"time": "<ISO>", "impact": "HIGH", "name": "...", "country": "..."}

    Cached for 5 minutes. Returns a blocking sentinel on error.
    """
    global _cache_ts, _cache_events

    now = datetime.now(timezone.utc)
    if _cache_ts and (now - _cache_ts).total_seconds() < _CACHE_TTL_SECONDS:
        return _cache_events

    events = await _fetch_from_fmp(now, lookahead_hours, api_key)
    _cache_ts = now
    _cache_events = events
    return events


async def _fetch_from_fmp(
    now: datetime,
    lookahead_hours: int,
    api_key: str | None,
) -> list[dict]:
    """Call FMP economic calendar API and normalise results."""
    key = api_key or os.environ.get("NEWS_API_KEY", "")

    if not key:
        logger.critical(
            "[news] NEWS_API_KEY not configured — returning block sentinel"
        )
        return [{"name": "NEWS_API_UNAVAILABLE", "impact": "HIGH", "time": now.isoformat()}]

    def _sentinel(reason: str) -> list[dict]:
        logger.warning(f"[news] {reason} — returning block sentinel")
        return [{"name": "NEWS_DATA_UNAVAILABLE", "impact": "HIGH", "time": now.isoformat()}]

    try:
        date_from = (now - timedelta(minutes=30)).strftime("%Y-%m-%d")
        date_to = (now + timedelta(hours=lookahead_hours)).strftime("%Y-%m-%d")
        url = f"{_FMP_BASE}?from={date_from}&to={date_to}&apikey={key}"

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        return _sentinel(f"FMP calendar fetch failed: {e}")

    if not isinstance(raw, list):
        return _sentinel(f"Unexpected FMP response type: {type(raw)}")

    events: list[dict] = []
    for item in raw:
        impact = (item.get("impact") or "").strip().upper()
        if impact not in ("HIGH", "MEDIUM", "LOW"):
            continue
        time_str = item.get("date") or item.get("time") or ""
        if not time_str:
            continue
        time_str = time_str.replace(" ", "T")
        if "+" not in time_str and "Z" not in time_str and not re.search(r'-\d{2}:\d{2}$', time_str):
            time_str += "+00:00"
        events.append({
            "time": time_str,
            "impact": impact,
            "name": item.get("event", "Unknown"),
            "country": item.get("country", ""),
        })

    logger.info(
        f"[news] Fetched {len(events)} economic events "
        f"({sum(1 for e in events if e['impact'] == 'HIGH')} HIGH impact)"
    )
    return events
