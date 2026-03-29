"""
News filter — blocks trading during high-impact news events.

Pipeline order: SECOND — skip expensive filters if news blackout active.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alphaloop.tools.base import BaseTool, ToolResult


class NewsFilter(BaseTool):
    """
    High-impact news event blackout filter.

    Blocks trading within +/- N minutes of HIGH or CRITICAL impact
    economic events. Events come from context.news (pre-fetched by
    the market context builder).
    """

    name = "news_filter"
    description = "Blocks trading during high-impact news events"

    async def run(self, context) -> ToolResult:
        news_events = context.news
        if not news_events:
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
