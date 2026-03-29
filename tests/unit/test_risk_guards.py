"""Tests for risk guards."""

from alphaloop.risk.guards import (
    SignalHashFilter,
    ConfidenceVarianceFilter,
    SpreadRegimeFilter,
    EquityCurveScaler,
    DrawdownPauseGuard,
)


class MockSignal:
    def __init__(self, direction="BUY", entry_zone=(2340.0, 2342.0)):
        self.direction = direction
        self.entry_zone = entry_zone
        self.timeframe = "H1"


def test_signal_hash_filter_no_duplicate():
    f = SignalHashFilter(window=3)
    sig = MockSignal()
    ctx = {"timeframes": {"H1": {"indicators": {"trend_bias": "bullish"}}}}
    assert f.is_duplicate("XAUUSD", sig, ctx) is False


def test_signal_hash_filter_duplicate():
    f = SignalHashFilter(window=3)
    sig = MockSignal()
    ctx = {"timeframes": {"H1": {"indicators": {"trend_bias": "bullish"}}}}
    f.is_duplicate("XAUUSD", sig, ctx)
    assert f.is_duplicate("XAUUSD", sig, ctx) is True


def test_confidence_variance_stable():
    f = ConfidenceVarianceFilter(window=3, max_stdev=0.15)
    f.record(0.85)
    f.record(0.87)
    f.record(0.86)
    assert f.is_unstable() is False


def test_confidence_variance_unstable():
    f = ConfidenceVarianceFilter(window=3, max_stdev=0.05)
    f.record(0.50)
    f.record(0.90)
    f.record(0.60)
    assert f.is_unstable() is True


def test_spread_regime_no_spike():
    f = SpreadRegimeFilter(window=50, threshold=1.8)
    for _ in range(20):
        f.record(3.0)
    assert f.is_spike(3.5) is False


def test_spread_regime_spike():
    f = SpreadRegimeFilter(window=50, threshold=1.8)
    for _ in range(20):
        f.record(3.0)
    assert f.is_spike(10.0) is True


def test_equity_curve_scaler_above_ma():
    e = EquityCurveScaler(window=5)
    for _ in range(5):
        e.record_pnl(100.0)
    assert e.risk_scale() == 1.0


def test_equity_curve_scaler_below_ma():
    e = EquityCurveScaler(window=5)
    e.record_pnl(200.0)
    e.record_pnl(100.0)
    e.record_pnl(-50.0)
    e.record_pnl(-100.0)
    e.record_pnl(-200.0)
    assert e.risk_scale() == 0.5


def test_drawdown_pause_no_trigger():
    g = DrawdownPauseGuard(pause_minutes=30)
    g.record_close(-10.0)
    g.record_close(50.0)
    assert g.is_paused() is False


def test_drawdown_pause_triggers():
    g = DrawdownPauseGuard(pause_minutes=30)
    g.record_close(-10.0)
    g.record_close(-20.0)
    g.record_close(-30.0)
    assert g.is_paused() is True
