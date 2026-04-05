"""
DST-aware session detection and scoring.
Sessions defined in exchange local time, converted to UTC dynamically.
"""

from datetime import datetime, time as _time, timezone
from zoneinfo import ZoneInfo

_TZ_LONDON = ZoneInfo("Europe/London")
_TZ_NY = ZoneInfo("America/New_York")

_LONDON_OPEN = _time(8, 0)
_LONDON_CLOSE = _time(16, 30)
_NY_OPEN = _time(9, 30)
_NY_CLOSE = _time(16, 0)
_ASIA_LATE = _time(4, 0)

# Default session weights
DEFAULT_SESSION_WEIGHTS = {
    "london_ny_overlap": 1.0,
    "ny_session": 0.85,
    "london_session": 0.85,
    "asia_late": 0.40,
    "asia_early": 0.20,
    "weekend": 0.0,
}


def utc_day_start(now: datetime | None = None) -> datetime:
    """Return the current UTC day boundary."""
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _session_active(now_utc: datetime, tz: ZoneInfo, open_t: _time, close_t: _time) -> bool:
    local = now_utc.astimezone(tz)
    return open_t <= local.time() < close_t


def _session_open_utc(now_utc: datetime, tz: ZoneInfo, open_t: _time) -> int:
    local = now_utc.astimezone(tz)
    local_open = local.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
    return local_open.astimezone(timezone.utc).hour


def get_session_info(
    now: datetime | None = None,
    session_weights: dict[str, float] | None = None,
    min_session_score: float = 0.50,
) -> dict:
    """Returns current session metadata including name, score, and active status."""
    if now is None:
        now = datetime.now(timezone.utc)

    weights = session_weights or DEFAULT_SESSION_WEIGHTS
    weekday = now.weekday()

    if weekday >= 5:
        return {"name": "weekend", "score": 0.0, "active": False, "hour_utc": now.hour, "minute": now.minute, "is_weekend": True, "is_london_ny_overlap": False}

    in_london = _session_active(now, _TZ_LONDON, _LONDON_OPEN, _LONDON_CLOSE)
    in_ny = _session_active(now, _TZ_NY, _NY_OPEN, _NY_CLOSE)

    if in_london and in_ny:
        name = "london_ny_overlap"
    elif in_ny:
        name = "ny_session"
    elif in_london:
        name = "london_session"
    elif now.hour >= _ASIA_LATE.hour or now.hour < _session_open_utc(now, _TZ_LONDON, _LONDON_OPEN):
        name = "asia_late" if now.hour >= _ASIA_LATE.hour else "asia_early"
    else:
        name = "asia_early"

    score = weights.get(name, 0.2)

    return {
        "name": name,
        "score": score,
        "active": score >= min_session_score,
        "hour_utc": now.hour,
        "minute": now.minute,
        "is_weekend": weekday >= 5,
        "is_london_ny_overlap": name == "london_ny_overlap",
    }


def get_session_name(now: datetime | None = None) -> str:
    return get_session_info(now)["name"]


def get_session_score_for_hour(utc_hour: int) -> float:
    """
    Quick session score lookup by UTC hour for backtest filtering.

    Approximate mapping (no DST adjustment in backtests):
      London: 08-16 UTC → 0.85
      NY: 14-21 UTC → 0.85
      Overlap: 14-16 UTC → 1.0
      Asia late: 04-08 UTC → 0.40
      Asia early: 00-04 UTC → 0.20
      Off hours: 21-00 → 0.20
    """
    if 14 <= utc_hour < 16:
        return 1.0   # London-NY overlap
    if 8 <= utc_hour < 14:
        return 0.85   # London
    if 16 <= utc_hour < 21:
        return 0.85   # NY
    if 4 <= utc_hour < 8:
        return 0.40   # Asia late
    return 0.20       # Asia early / off-hours
