"""Tests for ATR calculation correctness in alphaloop.data.indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from alphaloop.data.indicators import atr


def _make_ohlc(
    n: int = 20,
    base_close: float = 100.0,
    spread: float = 2.0,
    step: float = 0.5,
) -> pd.DataFrame:
    """Create synthetic OHLC data with predictable high/low/close values.

    Each bar:
        close = base_close + i * step
        high  = close + spread
        low   = close - spread
    This gives a constant True Range of 2 * spread for bars after the first
    (since |high - prev_close| and |low - prev_close| will be <= 2*spread + step).
    """
    rows = []
    for i in range(n):
        c = base_close + i * step
        h = c + spread
        lo = c - spread
        o = c - step / 2
        rows.append({"open": o, "high": h, "low": lo, "close": c})
    return pd.DataFrame(rows)


def _true_range_series(df: pd.DataFrame) -> pd.Series:
    """Manually compute True Range for verification."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


# ── Basic ATR correctness ────────────────────────────────────────────────────


def test_atr_returns_series():
    """atr() returns a pandas Series of the same length as input."""
    df = _make_ohlc(30)
    result = atr(df, period=14)
    assert isinstance(result, pd.Series)
    assert len(result) == len(df)


def test_atr_matches_manual_true_range():
    """ATR computed via indicators.atr matches manual TR-based EWM."""
    df = _make_ohlc(30)
    tr = _true_range_series(df)
    expected = tr.ewm(com=14 - 1, adjust=False).mean()
    result = atr(df, period=14)
    pd.testing.assert_series_equal(result, expected, check_names=False)


def test_atr_with_constant_bars():
    """When all bars are identical, ATR should converge toward high - low."""
    n = 50
    df = pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [102.0] * n,
            "low": [98.0] * n,
            "close": [100.0] * n,
        }
    )
    result = atr(df, period=14)
    # For constant bars: TR = high - low = 4.0 every bar
    # EWM should converge to 4.0
    assert abs(result.iloc[-1] - 4.0) < 0.01


def test_atr_period_5():
    """ATR with a shorter period (5) matches manual computation."""
    df = _make_ohlc(20)
    tr = _true_range_series(df)
    expected = tr.ewm(com=5 - 1, adjust=False).mean()
    result = atr(df, period=5)
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ── Edge cases ───────────────────────────────────────────────────────────────


def test_atr_early_bars_not_nan_after_first():
    """EWM-based ATR only has NaN for bar 0 (no prev_close), rest are filled."""
    df = _make_ohlc(10)
    result = atr(df, period=14)
    # Bar 0 has NaN prev_close so TR uses high-low only; EWM handles it
    # From bar 1 onward, values should not be NaN
    assert not result.iloc[1:].isna().any()


def test_atr_minimum_bars():
    """ATR works even with very few bars (e.g. 3)."""
    df = _make_ohlc(3)
    result = atr(df, period=14)
    assert len(result) == 3
    assert not result.iloc[1:].isna().any()


def test_atr_large_gap():
    """A large gap between bars produces a spike in ATR."""
    df = _make_ohlc(20, spread=2.0, step=0.5)
    # Inject a large gap at bar 10
    df.loc[10, "high"] = 200.0
    df.loc[10, "low"] = 90.0
    result = atr(df, period=14)
    # ATR at bar 10 should be higher than surrounding bars
    assert result.iloc[10] > result.iloc[9]


def test_atr_all_positive():
    """ATR values should always be non-negative."""
    df = _make_ohlc(50)
    result = atr(df, period=14)
    assert (result.dropna() >= 0).all()
