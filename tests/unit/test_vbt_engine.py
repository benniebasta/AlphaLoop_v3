"""Unit tests for backtester/vbt_engine.py — vectorbt + TradeConstructor backtest."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

from alphaloop.backtester.vbt_engine import run_vectorbt_backtest, VBTBacktestResult
from alphaloop.config.assets import get_asset_config


def _make_ohlcv(n=200, base_price=2700.0, trend_strength=0.5):
    """Generate synthetic OHLCV data with swing structure for XAUUSD."""
    np.random.seed(42)
    times = [datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * i) for i in range(n)]

    # Generate a price series with some swings
    close = np.zeros(n)
    close[0] = base_price
    for i in range(1, n):
        # Add a sine wave for swing structure + random noise
        wave = np.sin(i / 20) * 15  # ~15 point swings
        noise = np.random.randn() * 2
        close[i] = close[i - 1] + trend_strength + wave * 0.1 + noise

    high = close + np.random.uniform(1, 5, n)
    low = close - np.random.uniform(1, 5, n)
    open_ = close + np.random.uniform(-2, 2, n)
    volume = np.random.randint(100, 1000, n).astype(float)

    df = pd.DataFrame({
        "time": times,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
    return df


class TestVBTBacktestBasic:
    def test_runs_without_error(self):
        """Basic smoke test — backtest should complete."""
        df = _make_ohlcv(200)
        params = {
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
            "ema_fast": 21,
            "ema_slow": 55,
            "tp1_rr": 1.5,
            "tp2_rr": 2.5,
        }
        result = run_vectorbt_backtest(df, params, symbol="XAUUSD")
        assert isinstance(result, VBTBacktestResult)
        assert result.error is None

    def test_insufficient_data_returns_error(self):
        """Too few bars should return error."""
        df = _make_ohlcv(30)
        result = run_vectorbt_backtest(df, {}, symbol="XAUUSD")
        assert result.error is not None
        assert "Insufficient" in result.error

    def test_construction_stats_populated(self):
        """Result should have construction stats even with 0 trades."""
        df = _make_ohlcv(200)
        params = {
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
        }
        result = run_vectorbt_backtest(df, params, symbol="XAUUSD")
        # execution_rate should be a valid number
        assert 0.0 <= result.execution_rate <= 1.0
        assert isinstance(result.skipped_by_reason, dict)


class TestConstructorUsedInBacktest:
    def test_no_atr_fallback_in_backtest(self):
        """Backtest should NOT use ATR-based SL — only structure."""
        df = _make_ohlcv(200)
        # Set impossibly tight bounds so no structure passes
        params = {
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
            "sl_min_points": 9999,
            "sl_max_points": 9999,
        }
        result = run_vectorbt_backtest(df, params, symbol="XAUUSD")
        # With impossible bounds, no trades should be constructed
        assert result.trade_count == 0
        assert result.valid_constructed == 0

    def test_skip_reasons_counted(self):
        """Construction failures should be categorized."""
        df = _make_ohlcv(200)
        # Very tight bounds to force skips
        params = {
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
            "sl_min_points": 5000,
            "sl_max_points": 5001,
        }
        result = run_vectorbt_backtest(df, params, symbol="XAUUSD")
        # If there were opportunities, they should have skip reasons
        if result.opportunities > 0:
            total_skipped = sum(result.skipped_by_reason.values())
            assert total_skipped > 0 or result.valid_constructed > 0


class TestPerformanceMetrics:
    def test_equity_curve_starts_at_balance(self):
        df = _make_ohlcv(200)
        result = run_vectorbt_backtest(df, {}, symbol="XAUUSD", balance=10_000.0)
        assert len(result.equity_curve) >= 1
        assert result.equity_curve[0] == 10_000.0

    def test_execution_rate_formula(self):
        """execution_rate = valid_constructed / opportunities."""
        df = _make_ohlcv(200)
        result = run_vectorbt_backtest(df, {}, symbol="XAUUSD")
        if result.opportunities > 0:
            expected = result.valid_constructed / result.opportunities
            assert result.execution_rate == pytest.approx(expected, abs=0.01)
