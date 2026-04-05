"""Unit tests for core/normalization.py — centralised distance normalization."""

import pytest

from alphaloop.core.normalization import DistanceInfo, normalize_distance, check_bounds


# ── normalize_distance ────────────────────────────────────────────────────────


class TestNormalizeDistance:
    def test_xauusd_distance(self):
        """XAUUSD: pip_size=0.1, entry=2750.0, sl=2730.0 → 200 pts."""
        info = normalize_distance(2750.0, 2730.0, pip_size=0.1)
        assert info.price_delta == pytest.approx(20.0, abs=0.01)
        assert info.points == pytest.approx(200.0, abs=0.1)
        assert info.pips == info.points

    def test_eurusd_distance(self):
        """EURUSD: pip_size=0.0001, entry=1.08500, sl=1.08200 → 30 pts/pips."""
        info = normalize_distance(1.08500, 1.08200, pip_size=0.0001)
        assert info.price_delta == pytest.approx(0.003, abs=0.0001)
        assert info.points == pytest.approx(30.0, abs=0.1)

    def test_btcusd_distance(self):
        """BTCUSD: pip_size=1.0, entry=65000, sl=64500 → 500 pts."""
        info = normalize_distance(65000.0, 64500.0, pip_size=1.0)
        assert info.price_delta == pytest.approx(500.0, abs=0.01)
        assert info.points == pytest.approx(500.0, abs=0.1)

    def test_atr_multiple_computed(self):
        info = normalize_distance(2750.0, 2730.0, pip_size=0.1, atr=10.0)
        assert info.atr_multiple == pytest.approx(2.0, abs=0.01)

    def test_atr_multiple_zero_when_no_atr(self):
        info = normalize_distance(2750.0, 2730.0, pip_size=0.1)
        assert info.atr_multiple == 0.0

    def test_zero_pip_size_raises(self):
        with pytest.raises(ValueError, match="pip_size must be positive"):
            normalize_distance(100, 99, pip_size=0)

    def test_negative_pip_size_raises(self):
        with pytest.raises(ValueError, match="pip_size must be positive"):
            normalize_distance(100, 99, pip_size=-0.1)

    def test_order_independent(self):
        """Distance should be the same regardless of which level is higher."""
        a = normalize_distance(2750.0, 2730.0, pip_size=0.1)
        b = normalize_distance(2730.0, 2750.0, pip_size=0.1)
        assert a.points == b.points


# ── check_bounds ──────────────────────────────────────────────────────────────


class TestCheckBounds:
    def test_within_bounds(self):
        info = DistanceInfo(price_delta=20.0, points=200.0, pips=200.0, atr_multiple=1.5)
        ok, reason = check_bounds(info, min_points=150, max_points=500)
        assert ok is True
        assert reason == ""

    def test_below_minimum(self):
        info = DistanceInfo(price_delta=1.0, points=10.0, pips=10.0, atr_multiple=0.1)
        ok, reason = check_bounds(info, min_points=150, max_points=500)
        assert ok is False
        assert "< min" in reason

    def test_above_maximum(self):
        info = DistanceInfo(price_delta=100.0, points=1000.0, pips=1000.0, atr_multiple=10.0)
        ok, reason = check_bounds(info, min_points=150, max_points=500)
        assert ok is False
        assert "> max" in reason

    def test_exactly_at_min(self):
        info = DistanceInfo(price_delta=15.0, points=150.0, pips=150.0, atr_multiple=1.0)
        ok, _ = check_bounds(info, min_points=150, max_points=500)
        assert ok is True

    def test_exactly_at_max(self):
        info = DistanceInfo(price_delta=50.0, points=500.0, pips=500.0, atr_multiple=3.0)
        ok, _ = check_bounds(info, min_points=150, max_points=500)
        assert ok is True
