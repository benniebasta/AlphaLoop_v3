"""Unit tests for backtester/vbt_engine.py — vectorbt + TradeConstructor backtest."""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from alphaloop.backtester.vbt_engine import (
    VBTBacktestResult,
    _build_backtest_strategy_payload,
    _configured_signal_logic,
    _configured_signal_rules,
    _resolve_backtest_setup_tag,
    run_vectorbt_backtest,
)
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
    def test_build_backtest_strategy_payload_keeps_flat_numeric_params_and_resolves_spec_first_rules(self):
        params = {
            "ema_fast": 13,
            "ema_slow": 34,
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
            "setup_family": "pullback_continuation",
            "source": "legacy_flat",
            "strategy_spec": {
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "entry_model": {
                    "signal_rule_sources": ["macd_crossover"],
                    "signal_logic": "OR",
                },
                "metadata": {
                    "source": "ui_ai_signal_card",
                },
            },
        }

        payload = _build_backtest_strategy_payload(params)

        assert payload["params"]["ema_fast"] == 13
        assert payload["params"]["ema_slow"] == 34
        assert payload["params"]["signal_rules"] == [{"source": "macd_crossover"}]
        assert payload["params"]["signal_logic"] == "OR"
        assert payload["strategy_spec"]["setup_family"] == "discretionary_ai"
        assert payload["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"

    def test_backtest_rule_loader_defaults_missing_rules_for_backward_compat(self):
        assert _configured_signal_rules({"signal_rules": None}) == [{"source": "ema_crossover"}]
        assert _configured_signal_logic({"signal_logic": None}) == "AND"

    def test_backtest_rule_loader_fails_closed_on_malformed_rules(self):
        assert _configured_signal_rules({"signal_rules": "ema_crossover"}) == []
        assert _configured_signal_logic({"signal_logic": "weird"}) == "AND"

    def test_backtest_rule_loader_prefers_strategy_spec_entry_model(self):
        params = {
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
            "strategy_spec": {
                "entry_model": {
                    "signal_rule_sources": ["macd_crossover"],
                    "signal_logic": "OR",
                }
            },
        }

        assert _configured_signal_rules(params) == [{"source": "macd_crossover"}]
        assert _configured_signal_logic(params) == "OR"

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
    def test_backtest_setup_tag_uses_flat_signal_rules_when_no_nested_params_exist(self):
        params = {
            "signal_mode": "algo_ai",
            "signal_rules": [{"source": "ema_crossover"}],
        }

        assert _resolve_backtest_setup_tag(params) == "momentum"

    def test_backtest_setup_tag_uses_list_style_tools(self):
        params = {
            "signal_mode": "algo_ai",
            "tools": ["bos_guard"],
            "signal_rules": [{"source": "ema_crossover"}],
        }

        assert _resolve_backtest_setup_tag(params) == "breakout"

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

    def test_backtest_uses_strategy_setup_family_for_hypothesis_setup_tag(self, monkeypatch):
        captured: list[str] = []

        def fake_construct(self, hypothesis, bid, ask, indicators, atr):
            captured.append(hypothesis.setup_tag)
            return SimpleNamespace(signal=None, rejection_reason="captured for test")

        monkeypatch.setattr(
            "alphaloop.pipeline.construction.TradeConstructor.construct",
            fake_construct,
        )

        df = _make_ohlcv(200)
        params = {
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
            "strategy_spec": {
                "signal_mode": "algo_ai",
                "setup_family": "momentum_expansion",
            },
        }

        run_vectorbt_backtest(df, params, symbol="XAUUSD")

        assert captured
        assert all(tag == "momentum" for tag in captured)


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
