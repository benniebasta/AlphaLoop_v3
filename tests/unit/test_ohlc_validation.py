"""Tests for OHLC data validation."""

import numpy as np
import pandas as pd

from alphaloop.data.validators import validate_ohlcv, detect_gaps


def _make_df(n=100, **overrides):
    """Generate a valid OHLCV DataFrame."""
    np.random.seed(42)
    close = 2000.0 + np.cumsum(np.random.randn(n) * 5)
    high = close + np.abs(np.random.randn(n) * 3)
    low = close - np.abs(np.random.randn(n) * 3)
    opn = close + np.random.randn(n) * 1
    # Ensure open is within [low, high]
    opn = np.clip(opn, low, high)
    vol = np.random.randint(100, 10000, n).astype(float)

    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    df = pd.DataFrame(
        {"open": opn, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )
    for col, vals in overrides.items():
        df[col] = vals
    return df


def test_valid_data_passes():
    df = _make_df()
    valid, issues = validate_ohlcv(df, symbol="XAUUSD")
    assert valid
    assert len(issues) == 0


def test_empty_df_fails():
    df = pd.DataFrame(columns=["open", "high", "low", "close"])
    valid, issues = validate_ohlcv(df)
    assert not valid
    assert any("Empty" in i for i in issues)


def test_missing_columns_fails():
    df = pd.DataFrame({"price": [1, 2, 3]})
    valid, issues = validate_ohlcv(df)
    assert not valid
    assert any("Missing" in i for i in issues)


def test_negative_prices_critical():
    df = _make_df(n=10)
    df.loc[df.index[0], "close"] = -100
    valid, issues = validate_ohlcv(df)
    assert not valid  # Negative prices are critical
    assert any("negative" in i for i in issues)


def test_high_less_than_low():
    df = _make_df(n=20)
    # Force a bar where high < low
    df.loc[df.index[5], "high"] = df.loc[df.index[5], "low"] - 10
    valid, issues = validate_ohlcv(df)
    # Should be flagged but not necessarily critical at low percentage
    assert len(issues) > 0
    assert any("High < Low" in i for i in issues)


def test_nan_values_flagged():
    df = _make_df(n=50)
    df.loc[df.index[0], "close"] = np.nan
    df.loc[df.index[1], "close"] = np.nan
    valid, issues = validate_ohlcv(df)
    assert any("NaN" in i for i in issues)


def test_zero_prices_flagged():
    df = _make_df(n=20)
    df.loc[df.index[3], "open"] = 0
    valid, issues = validate_ohlcv(df)
    assert any("zero" in i for i in issues)


def test_gap_detection():
    # Create normal 5min bars, then skip 3 bars to create a 20-min gap
    idx1 = pd.date_range("2024-01-01", periods=5, freq="5min")
    idx2 = pd.date_range("2024-01-01 00:40:00", periods=5, freq="5min")
    idx = idx1.append(idx2)
    n = len(idx)
    df = pd.DataFrame(
        {
            "open": range(n),
            "high": range(n),
            "low": range(n),
            "close": range(n),
        },
        index=idx,
    )
    gaps = detect_gaps(df, expected_interval_minutes=5, max_gap_multiple=3.0)
    assert len(gaps) == 1
    assert gaps[0]["gap_minutes"] == 20.0


def test_strict_mode():
    df = _make_df(n=20)
    df.loc[df.index[3], "open"] = 0  # Zero price = non-critical issue
    valid_normal, _ = validate_ohlcv(df, strict=False)
    valid_strict, _ = validate_ohlcv(df, strict=True)
    assert valid_normal  # Non-strict passes with warnings
    assert not valid_strict  # Strict fails on any issue
