"""
data/news.py
Async economic calendar fetcher — multi-provider with ForexFactory fallback.

Supported providers (set NEWS_PROVIDER in Settings):
  forexfactory  — free, no key required (default & fallback)
  finnhub       — requires API key (finnhub.io free tier; economic calendar needs paid plan)
  fmp           — requires API key (financialmodelingprep.com; calendar needs paid plan)

If the configured provider fails or has no key, automatically falls back to ForexFactory.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FF_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_FF_NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

# Module-level cache (shared across all providers)
_cache_ts: Optional[datetime] = None
_cache_events: list[dict] = []
_CACHE_TTL_SECONDS = 1800


async def fetch_upcoming_news(
    lookahead_hours: int = 4,
    api_key: str | None = None,
    provider: str = "forexfactory",
    finnhub_key: str | None = None,
    fmp_key: str | None = None,
) -> list[dict]:
    """
    Fetch upcoming economic events using the configured provider.

    Falls back to ForexFactory if the primary provider fails or has no key.
    Returns list of dicts: {"time": "<ISO>", "impact": "HIGH"/"MEDIUM"/"LOW", "name": "...", "country": "..."}
    Cached for 5 minutes.
    """
    global _cache_ts, _cache_events

    now = datetime.now(timezone.utc)
    if _cache_ts and (now - _cache_ts).total_seconds() < _CACHE_TTL_SECONDS:
        return _cache_events

    prov = (provider or os.environ.get("NEWS_PROVIDER", "forexfactory")).lower()

    # Resolve key for the active provider (explicit arg > env var)
    if prov == "finnhub":
        key = finnhub_key or os.environ.get("FINNHUB_API_KEY", "")
    elif prov == "fmp":
        key = fmp_key or os.environ.get("FMP_API_KEY", "")
    else:
        key = ""

    events: list[dict] | None = None

    if prov == "finnhub" and key:
        events = await _fetch_from_finnhub(now, lookahead_hours, key)
        if events is None:
            logger.warning("[news] Finnhub failed — falling back to ForexFactory")
    elif prov == "fmp" and key:
        events = await _fetch_from_fmp(now, lookahead_hours, key)
        if events is None:
            logger.warning("[news] FMP failed — falling back to ForexFactory")
    elif prov in ("finnhub", "fmp") and not key:
        logger.warning("[news] %s selected but no API key set — using ForexFactory", prov)

    if events is None:
        events = await _fetch_from_ff(now, lookahead_hours)

    # Final fallback if everything failed
    if events is None:
        real_cache = [e for e in _cache_events if e.get("name") != "NEWS_DATA_UNAVAILABLE"]
        if real_cache:
            age = f"{(now - _cache_ts).total_seconds():.0f}s" if _cache_ts else "unknown"
            logger.warning(
                "[news] All providers failed — using stale cache (%d events, age %s)",
                len(real_cache),
                age,
            )
            # Don't update _cache_ts so the next call retries providers immediately
            return real_cache
        logger.warning("[news] All providers failed and no real cache — returning block sentinel")
        # Don't cache the sentinel so real events aren't overwritten
        return [{"name": "NEWS_DATA_UNAVAILABLE", "impact": "HIGH", "time": now.isoformat()}]

    _cache_ts = now
    _cache_events = events
    return events


# ── ForexFactory ──────────────────────────────────────────────────────────────

_FF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _fetch_from_ff(now: datetime, lookahead_hours: int) -> list[dict] | None:
    """Fetch ForexFactory calendar (this week + next week). No key required."""
    try:
        async with httpx.AsyncClient(timeout=5.0, headers=_FF_HEADERS) as client:
            import asyncio as _asyncio

            async def _get(url: str) -> list:
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    return r.json()
                except Exception:
                    return []

            this_week, next_week = await _asyncio.gather(_get(_FF_THIS_WEEK), _get(_FF_NEXT_WEEK))
            raw = (this_week or []) + (next_week or [])
    except Exception as e:
        logger.warning("[news] ForexFactory fetch failed: %s", e)
        return None

    if not raw:
        return None

    lookahead_cutoff = now + timedelta(hours=lookahead_hours)
    lookback_cutoff = now - timedelta(minutes=30)

    events: list[dict] = []
    for item in raw:
        impact = (item.get("impact") or "").strip().capitalize()
        if impact not in ("High", "Medium", "Low"):
            continue
        time_str = _normalise_time(item.get("date") or "")
        if not time_str:
            continue
        try:
            event_dt = datetime.fromisoformat(time_str)
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if event_dt < lookback_cutoff or event_dt > lookahead_cutoff:
            continue
        events.append({
            "time": event_dt.isoformat(),
            "impact": impact.upper(),
            "name": item.get("title", "Unknown"),
            "country": item.get("country", ""),
            "source": "forexfactory",
        })

    logger.info("[news] ForexFactory: %d events in window (%d HIGH)", len(events),
                sum(1 for e in events if e["impact"] == "HIGH"))
    return events


# ── Finnhub ───────────────────────────────────────────────────────────────────

async def _fetch_from_finnhub(now: datetime, lookahead_hours: int, key: str) -> list[dict] | None:
    """Fetch Finnhub economic calendar. Requires paid plan for this endpoint."""
    try:
        date_from = (now - timedelta(minutes=30)).strftime("%Y-%m-%d")
        date_to = (now + timedelta(hours=lookahead_hours)).strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/calendar/economic?from={date_from}&to={date_to}&token={key}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        logger.warning("[news] Finnhub fetch failed: %s", e)
        return None

    if not isinstance(raw, dict) or "economicCalendar" not in raw:
        logger.warning("[news] Unexpected Finnhub response: %s", str(raw)[:80])
        return None

    events: list[dict] = []
    for item in raw["economicCalendar"]:
        impact = (item.get("impact") or "").strip().upper()
        if impact not in ("HIGH", "MEDIUM", "LOW"):
            continue
        time_str = _normalise_time(item.get("time") or "")
        if not time_str:
            continue
        events.append({
            "time": time_str,
            "impact": impact,
            "name": item.get("event", "Unknown"),
            "country": item.get("country", ""),
            "source": "finnhub",
        })

    logger.info("[news] Finnhub: %d events (%d HIGH)", len(events),
                sum(1 for e in events if e["impact"] == "HIGH"))
    return events


# ── FMP ───────────────────────────────────────────────────────────────────────

async def _fetch_from_fmp(now: datetime, lookahead_hours: int, key: str) -> list[dict] | None:
    """Fetch FMP economic calendar. Requires paid plan for this endpoint."""
    try:
        date_from = (now - timedelta(minutes=30)).strftime("%Y-%m-%d")
        date_to = (now + timedelta(hours=lookahead_hours)).strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={date_from}&to={date_to}&apikey={key}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as e:
        logger.warning("[news] FMP fetch failed: %s", e)
        return None

    if isinstance(raw, dict):
        # Subscription error or unexpected response
        logger.warning("[news] FMP response error: %s", str(raw)[:120])
        return None

    if not isinstance(raw, list):
        return None

    events: list[dict] = []
    for item in raw:
        impact = (item.get("impact") or "").strip().upper()
        if impact not in ("HIGH", "MEDIUM", "LOW"):
            continue
        time_str = _normalise_time(item.get("date") or item.get("time") or "")
        if not time_str:
            continue
        events.append({
            "time": time_str,
            "impact": impact,
            "name": item.get("event", "Unknown"),
            "country": item.get("country", ""),
            "source": "fmp",
        })

    logger.info("[news] FMP: %d events (%d HIGH)", len(events),
                sum(1 for e in events if e["impact"] == "HIGH"))
    return events


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_time(time_str: str) -> str:
    """Normalise a time string to ISO format with UTC offset."""
    if not time_str:
        return ""
    time_str = time_str.replace(" ", "T")
    if "+" not in time_str and "Z" not in time_str and not re.search(r'-\d{2}:\d{2}$', time_str):
        time_str += "+00:00"
    return time_str
