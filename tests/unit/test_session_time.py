"""Tests for session time detection."""

from datetime import datetime, timezone
from alphaloop.utils.time import get_session_info, get_session_name


def test_weekend():
    # Saturday
    sat = datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)
    info = get_session_info(sat)
    assert info["name"] == "weekend"
    assert info["score"] == 0.0
    assert info["active"] is False


def test_weekday_has_session():
    # Wednesday 14:00 UTC — should be London or overlap
    wed = datetime(2024, 1, 3, 14, 0, tzinfo=timezone.utc)
    info = get_session_info(wed)
    assert info["name"] in ("london_session", "london_ny_overlap", "ny_session")
    assert info["score"] > 0


def test_get_session_name():
    sat = datetime(2024, 1, 6, 12, 0, tzinfo=timezone.utc)
    assert get_session_name(sat) == "weekend"
