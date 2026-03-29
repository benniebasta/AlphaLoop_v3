"""
Unit tests for data/indicators.py — RSI, EMA, ATR calculations.
"""

import numpy as np
import pandas as pd
import pytest

from alphaloop.data.indicators import atr, detect_bos, detect_fvg, ema, rsi, vwap


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ohlcv(n: int = 100, base_price: float = 2000.0, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.RandomState(seed)
    prices = base_price + np.cumsum(rng.randn(n) * 2)
    highs = prices + rng.uniform(0.5, 3.0, n)
    lows = prices - rng.uniform(0.5, 3.0, n)
    opens = prices + rng.randn(n) * 0.5
    volumes = rng.randint(100, 10000, n).astype(float)
    times = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")

    return pd.DataFrame({
        "time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "volume": volumes,
    })


# ── EMA tests ─────────────────────────────────────────────────────────────────


class TestEMA:
    def test_ema_returns_series(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(series, 3)
        assert isinstance(result, pd.Series)
        assert len(result) == len(series)

    def test_ema_no_nans(self):
        series = pd.Series(range(1, 21), dtype=float)
        result = ema(series, 5)
        assert not result.isna().any()

    def test_ema_converges_to_constant(self):
        series = pd.Series([10.0] * 50)
        result = ema(series, 10)
        assert abs(result.iloc[-1] - 10.0) < 1e-10

    def test_ema_shorter_period_more_responsive(self):
        series = pd.Series([1.0] * 20 + [10.0] * 20)
        fast = ema(series, 5)
        slow = ema(series, 20)
        # After the jump, fast EMA should be closer to 10
        assert fast.iloc[-1] > slow.iloc[-1]


# ── RSI tests ─────────────────────────────────────────────────────────────────


class TestRSI:
    def test_rsi_range(self):
        df = _make_ohlcv(200)
        result = rsi(df["close"], 14)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_overbought_on_rising(self):
        """Monotonically rising prices should produce RSI near 100."""
        series = pd.Series(np.arange(1, 101, dtype=float))
        result = rsi(series, 14)
        assert result.iloc[-1] > 90

    def test_rsi_oversold_on_falling(self):
        """Monotonically falling prices should produce RSI near 0."""
        series = pd.Series(np.arange(100, 0, -1, dtype=float))
        result = rsi(series, 14)
        assert result.iloc[-1] < 10

    def test_rsi_midpoint_on_flat(self):
        """Alternating up/down should keep RSI near 50."""
        pattern = [100 + (i % 2) for i in range(100)]
        series = pd.Series(pattern, dtype=float)
        result = rsi(series, 14)
        assert 40 < result.iloc[-1] < 60


# ── ATR tests ─────────────────────────────────────────────────────────────────


class TestATR:
    def test_atr_positive(self):
        df = _make_ohlcv(100)
        result = atr(df, 14)
        valid = result.dropna()
        assert (valid > 0).all()

    def test_atr_length(self):
        df = _make_ohlcv(100)
        result = atr(df, 14)
        assert len(result) == len(df)

    def test_atr_increases_with_volatility(self):
        """Higher volatility data should produce higher ATR."""
        df_calm = _make_ohlcv(100, seed=1)
        df_wild = df_calm.copy()
        df_wild["high"] = df_wild["high"] + 20  # widen range
        df_wild["low"] = df_wild["low"] - 20

        atr_calm = atr(df_calm, 14).iloc[-1]
        atr_wild = atr(df_wild, 14).iloc[-1]
        assert atr_wild > atr_calm


# ── BOS tests ─────────────────────────────────────────────────────────────────


class TestDetectBOS:
    def test_insufficient_data(self):
        df = _make_ohlcv(10)
        result = detect_bos(df, atr_val=5.0, lookback=20)
        assert not result["bullish_bos"]
        assert not result["bearish_bos"]

    def test_bullish_bos_detected(self):
        """Spike the last close well above recent highs."""
        df = _make_ohlcv(50, base_price=100.0, seed=10)
        # Force a breakout: last close far above recent highs
        df.loc[df.index[-1], "close"] = df["high"].iloc[:-1].max() + 50
        atr_val = float(atr(df, 14).iloc[-1])
        result = detect_bos(df, atr_val, lookback=20)
        assert result["bullish_bos"]

    def test_zero_atr_returns_no_bos(self):
        df = _make_ohlcv(50)
        result = detect_bos(df, atr_val=0.0, lookback=20)
        assert not result["bullish_bos"]
        assert not result["bearish_bos"]


# ── FVG tests ─────────────────────────────────────────────────────────────────


class TestDetectFVG:
    def test_no_fvg_on_short_data(self):
        df = _make_ohlcv(2)
        result = detect_fvg(df, atr_val=5.0)
        assert not result["has_bullish"]
        assert not result["has_bearish"]

    def test_fvg_structure(self):
        df = _make_ohlcv(100)
        atr_val = float(atr(df, 14).iloc[-1])
        result = detect_fvg(df, atr_val)
        assert "bullish" in result
        assert "bearish" in result
        assert isinstance(result["has_bullish"], bool)


# ── VWAP tests ────────────────────────────────────────────────────────────────


class TestVWAP:
    def test_vwap_returns_series(self):
        df = _make_ohlcv(100)
        result = vwap(df)
        assert isinstance(result, pd.Series)
        assert len(result) == len(df)

    def test_vwap_within_price_range(self):
        """VWAP should be between low and high of the day."""
        df = _make_ohlcv(100)
        result = vwap(df)
        valid = result.dropna()
        assert len(valid) > 0
        # VWAP is a weighted average of typical price — should be reasonable
        assert valid.iloc[-1] > df["low"].min() * 0.9
        assert valid.iloc[-1] < df["high"].max() * 1.1
