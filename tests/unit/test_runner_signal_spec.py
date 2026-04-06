from __future__ import annotations

import numpy as np
import pytest

from alphaloop.backtester.params import BacktestParams
from alphaloop.backtester.runner import _ema_from_array, _run_vbt, make_signal_fn


def test_ema_from_array_handles_leading_nans():
    arr = np.array([np.nan, np.nan, 1.0, 2.0, 3.0], dtype=float)

    out = _ema_from_array(arr, 3)

    assert np.isnan(out[0])
    assert np.isnan(out[1])
    assert out[2] == pytest.approx(1.0)
    assert np.isfinite(out[3])
    assert np.isfinite(out[4])


@pytest.mark.asyncio
async def test_make_signal_fn_prefers_strategy_spec_entry_model_rules_and_logic(monkeypatch):
    captured: dict[str, object] = {}

    def fail_ema(*args, **kwargs):
        raise AssertionError("ema_crossover should not be used when strategy_spec entry_model overrides it")

    def fake_macd(*args, **kwargs):
        return True, False

    def fake_combine(rule_results, signal_logic):
        captured["signal_logic"] = signal_logic
        return True, False

    monkeypatch.setattr("alphaloop.signals.conditions.check_ema_crossover", fail_ema)
    monkeypatch.setattr("alphaloop.signals.conditions.check_macd_crossover", fake_macd)
    monkeypatch.setattr("alphaloop.signals.conditions.combine", fake_combine)

    params = BacktestParams(
        ema_fast=12,
        ema_slow=26,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
        strategy_spec={
            "signal_mode": "algo_ai",
            "setup_family": "momentum_expansion",
            "entry_model": {
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        },
    )

    signal_fn = make_signal_fn(params, filters=[])

    n = 90
    opens = np.linspace(100.0, 130.0, n)
    highs = opens + 1.0
    lows = opens - 1.0
    closes = opens + 0.5

    result = await signal_fn(70, opens, highs, lows, closes, [])

    assert result is not None
    assert result[5] == "macd_crossover"
    assert captured["signal_logic"] == "OR"


def test_run_vbt_passes_canonical_spec_first_params(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_vectorbt_backtest(df, params, **kwargs):
        captured["params"] = params
        return "ok"

    monkeypatch.setattr("alphaloop.backtester.runner.run_vectorbt_backtest", fake_run_vectorbt_backtest)

    params = BacktestParams(
        signal_mode="algo_ai",
        setup_family="pullback_continuation",
        source="stale_source",
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
        strategy_spec={
            "signal_mode": "algo_ai",
            "setup_family": "momentum_expansion",
            "metadata": {"source": "ui_ai_signal_card"},
            "entry_model": {
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        },
        tools={"fast_fingers": True},
    )

    opens = np.array([1.0, 2.0, 3.0], dtype=float)
    highs = opens + 0.1
    lows = opens - 0.1
    closes = opens + 0.05

    result = _run_vbt("XAUUSD", opens, highs, lows, closes, None, 10_000.0, params)

    assert result == "ok"
    passed = captured["params"]
    assert passed["setup_family"] == "momentum_expansion"
    assert passed["source"] == "ui_ai_signal_card"
    assert passed["signal_rules"] == [{"source": "macd_crossover"}]
    assert passed["signal_logic"] == "OR"
    assert passed["strategy_spec"]["setup_family"] == "momentum_expansion"


def test_run_vbt_preserves_none_signal_rules_as_default_ema(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_vectorbt_backtest(df, params, **kwargs):
        captured["params"] = params
        return "ok"

    monkeypatch.setattr("alphaloop.backtester.runner.run_vectorbt_backtest", fake_run_vectorbt_backtest)

    params = BacktestParams.model_construct(
        signal_mode="algo_ai",
        setup_family="",
        source="legacy_source",
        signal_rules=None,
        signal_logic="AND",
        strategy_spec={},
        tools={},
        ema_fast=21,
        ema_slow=55,
        sl_atr_mult=2.0,
        tp1_rr=2.0,
        tp2_rr=4.0,
        rsi_period=14,
        rsi_ob=70.0,
        rsi_os=30.0,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        bb_period=20,
        bb_std_dev=2.0,
        adx_period=14,
        adx_min_threshold=20.0,
        volume_ma_period=20,
        risk_pct=0.01,
    )

    opens = np.array([1.0, 2.0, 3.0], dtype=float)
    highs = opens + 0.1
    lows = opens - 0.1
    closes = opens + 0.05

    result = _run_vbt("XAUUSD", opens, highs, lows, closes, None, 10_000.0, params)

    assert result == "ok"
    passed = captured["params"]
    assert passed["signal_rules"] == [{"source": "ema_crossover"}]
    assert passed["setup_family"] == "trend_continuation"
