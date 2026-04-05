"""
News filter — blocks trading during high-impact news events.

Pipeline order: SECOND — skip expensive filters if news blackout active.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

logger = logging.getLogger(__name__)


class NewsFilter(BaseTool):
    """
    High-impact news event blackout filter.

    Blocks trading within +/- N minutes of HIGH or CRITICAL impact
    economic events. Events come from context.news (pre-fetched by
    the market context builder).
    """

    name = "news_filter"
    description = "Blocks trading during high-impact news events"

    # When True (default), block trading if the news provider is unreachable.
    # Set to False to allow trading through data outages.
    block_on_data_unavailable: bool = True
    _alerted_unavailable: bool = False  # suppress repeated alerts

    async def run(self, context) -> ToolResult:
        news_events = context.news

        # Sentinel returned by news.py when all providers fail
        if news_events and news_events[0].get("name") == "NEWS_DATA_UNAVAILABLE":
            if not self._alerted_unavailable:
                logger.critical(
                    "[news_filter] NEWS_DATA_UNAVAILABLE — all trades are blocked. "
                    "Ensure FINNHUB_API_KEY or NEWS_API_KEY is configured in Settings. "
                    "Set block_on_data_unavailable=False to allow trading through outages."
                )
                self._alerted_unavailable = True
            if self.block_on_data_unavailable:
                return ToolResult(
                    passed=False,
                    reason="News data unavailable — blocked as precaution",
                    severity="block",
                    size_modifier=0.0,
                    data={"sentinel": True},
                )
            return ToolResult(
                passed=True,
                reason="News data unavailable — passing (configured)",
                data={"sentinel": True},
            )

        if not news_events:
            self._alerted_unavailable = False  # reset so future outages re-alert
            return ToolResult(
                passed=True,
                reason="No upcoming news events in window",
                data={"events_checked": 0},
            )

        pre_window = 30  # minutes before event
        post_window = 30  # minutes after event
        now = datetime.now(timezone.utc)
        blocked_events: list[dict] = []

        for event in news_events:
            impact = (event.get("impact") or "").upper()
            if impact not in ("HIGH", "CRITICAL"):
                continue

            event_time = _parse_event_time(event.get("time", ""), now)
            if event_time is None:
                # Unparseable HIGH event — block as precaution
                blocked_events.append({
                    "name": event.get("name", "Unknown"),
                    "impact": impact,
                    "minutes_to_event": 0.0,
                })
                continue

            diff_minutes = (event_time - now).total_seconds() / 60.0
            if -post_window <= diff_minutes <= pre_window:
                blocked_events.append({
                    "name": event.get("name", "Unknown"),
                    "impact": impact,
                    "minutes_to_event": round(diff_minutes, 1),
                })

        if blocked_events:
            names = ", ".join(e["name"] for e in blocked_events[:2])
            mins = blocked_events[0]["minutes_to_event"]
            direction = "in" if mins > 0 else "ago"
            return ToolResult(
                passed=False,
                reason=f"News blackout: {names} ({abs(mins):.0f}m {direction})",
                severity="block",
                size_modifier=0.0,
                data={
                    "active_events": blocked_events,
                    "pre_window_min": pre_window,
                    "post_window_min": post_window,
                },
            )

        return ToolResult(
            passed=True,
            reason=f"No HIGH/CRITICAL events within +/-{pre_window}m window",
            data={"events_checked": len(news_events)},
        )


    async def extract_features(self, context) -> FeatureResult:
        news_events = context.news
        if not news_events:
            return FeatureResult(
                group="volatility",
                features={"news_safety": 100.0},
                meta={"events_checked": 0},
            )

        now = datetime.now(timezone.utc)
        min_distance = float("inf")  # minutes to nearest HIGH/CRITICAL event

        for event in news_events:
            impact = (event.get("impact") or "").upper()
            if impact not in ("HIGH", "CRITICAL"):
                continue
            event_time = _parse_event_time(event.get("time", ""), now)
            if event_time is None:
                min_distance = 0  # unparseable = assume imminent
                break
            diff = abs((event_time - now).total_seconds() / 60.0)
            min_distance = min(min_distance, diff)

        if min_distance == float("inf"):
            # No HIGH/CRITICAL events found
            return FeatureResult(
                group="volatility",
                features={"news_safety": 100.0},
                meta={"events_checked": len(news_events), "nearest_high_min": None},
            )

        # news_safety: 100 = far from news, 0 = event imminent
        # Linear decay from 100 (60+ min away) to 0 (at event)
        safety = min(100.0, max(0.0, min_distance / 60 * 100))

        return FeatureResult(
            group="volatility",
            features={"news_safety": round(safety, 1)},
            meta={"events_checked": len(news_events), "nearest_high_min": round(min_distance, 1)},
        )


def _parse_event_time(time_str: str, reference: datetime) -> datetime | None:
    """Parse event time string into a datetime."""
    if not time_str:
        return None
    try:
        if "T" in time_str:
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        h, m = map(int, time_str.split(":"))
        dt = reference.replace(hour=h, minute=m, second=0, microsecond=0)
        if (dt - reference).total_seconds() < -12 * 3600:
            dt = dt + timedelta(days=1)
        return dt
    except Exception:
        return None
