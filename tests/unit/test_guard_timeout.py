"""Tests for DrawdownPauseGuard timeout behaviour and EquityCurveScaler."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from alphaloop.risk.guards import DrawdownPauseGuard, EquityCurveScaler


# ---------------------------------------------------------------------------
# DrawdownPauseGuard
# ---------------------------------------------------------------------------


class TestDrawdownPauseGuard:
    """Verify pause activation, timeout expiry, and non-trigger scenarios."""

    def test_three_accelerating_losses_trigger_pause(self):
        """3 consecutive losses with increasing magnitude must activate pause."""
        guard = DrawdownPauseGuard(pause_minutes=30)
        guard.record_close(-100)
        guard.record_close(-200)
        guard.record_close(-300)
        assert guard.is_paused() is True

    def test_pause_expires_after_pause_minutes(self):
        """After pause_minutes elapses, is_paused() must return False.

        Note: the guard re-evaluates the loss window on every call, so we
        record a small win after triggering the pause to break the
        accelerating-loss pattern.  The pause timer itself must still
        hold until pause_minutes has elapsed.
        """
        guard = DrawdownPauseGuard(pause_minutes=30)
        guard.record_close(-100)
        guard.record_close(-200)
        guard.record_close(-300)
        assert guard.is_paused() is True

        # A win breaks the 3-accelerating-loss pattern, but the
        # time-based pause is still active.
        guard.record_close(10)
        assert guard.is_paused() is True  # still within the 30-min window

        # Advance time past the pause window
        future = datetime.now(timezone.utc) + timedelta(minutes=31)
        with patch("alphaloop.risk.guards.datetime") as mock_dt:
            mock_dt.now.return_value = future
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert guard.is_paused() is False

    def test_win_after_two_losses_does_not_pause(self):
        """A win breaking the losing streak must prevent pause activation."""
        guard = DrawdownPauseGuard(pause_minutes=30)
        guard.record_close(-100)
        guard.record_close(-200)
        guard.record_close(50)  # win resets the streak
        assert guard.is_paused() is False

    def test_three_losses_without_acceleration_does_not_pause(self):
        """3 losses that do NOT accelerate should not trigger pause."""
        guard = DrawdownPauseGuard(pause_minutes=30)
        guard.record_close(-300)
        guard.record_close(-200)
        guard.record_close(-100)  # decreasing magnitude
        assert guard.is_paused() is False

    def test_fewer_than_three_losses_does_not_pause(self):
        """Fewer than 3 recorded closes should never trigger pause."""
        guard = DrawdownPauseGuard(pause_minutes=30)
        guard.record_close(-100)
        guard.record_close(-200)
        assert guard.is_paused() is False


# ---------------------------------------------------------------------------
# EquityCurveScaler
# ---------------------------------------------------------------------------


class TestEquityCurveScaler:
    """Verify risk scaling based on equity vs moving average."""

    def test_returns_half_when_equity_below_ma(self):
        """When cumulative equity is below its moving average, scale = 0.5."""
        scaler = EquityCurveScaler(window=5)
        # First few wins push the MA up, then losses drag equity below it.
        for pnl in [100, 100, 100, -300, -300]:
            scaler.record_pnl(pnl)
        assert scaler.risk_scale() == 0.5

    def test_returns_one_when_equity_above_ma(self):
        """When cumulative equity is at or above its moving average, scale = 1.0."""
        scaler = EquityCurveScaler(window=5)
        # Steadily rising equity stays above its own MA.
        for pnl in [100, 100, 100, 100, 100]:
            scaler.record_pnl(pnl)
        assert scaler.risk_scale() == 1.0

    def test_returns_one_when_insufficient_data(self):
        """Before the window is full, risk_scale must default to 1.0."""
        scaler = EquityCurveScaler(window=20)
        scaler.record_pnl(-500)
        scaler.record_pnl(-500)
        assert scaler.risk_scale() == 1.0
