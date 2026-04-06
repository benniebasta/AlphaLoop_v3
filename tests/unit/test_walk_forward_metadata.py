from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from alphaloop.backtester.params import BacktestParams
from alphaloop.backtester.walk_forward import WalkForwardEngine, _with_strategy_metadata


def _ohlcv(n: int = 120) -> pd.DataFrame:
    base = pd.Series(range(n), dtype=float)
    return pd.DataFrame(
        {
            "open": 2000.0 + base,
            "high": 2002.0 + base,
            "low": 1998.0 + base,
            "close": 2001.0 + base,
            "volume": 100.0,
        }
    )


def test_with_strategy_metadata_preserves_non_tuned_fields():
    base = BacktestParams(
        signal_mode="ai_signal",
        setup_family="discretionary_ai",
        strategy_spec={"setup_family": "discretionary_ai", "signal_mode": "ai_signal"},
        tools={"session_filter": True},
        source="meta_loop",
    )

    merged = _with_strategy_metadata({"ema_fast": 12, "ema_slow": 34}, base)

    assert merged["signal_mode"] == "ai_signal"
    assert merged["setup_family"] == "discretionary_ai"
    assert merged["strategy_spec"]["setup_family"] == "discretionary_ai"
    assert merged["tools"] == {"session_filter": True}
    assert merged["source"] == "meta_loop"


def test_with_strategy_metadata_normalizes_list_style_tools():
    base = SimpleNamespace(
        signal_mode="algo_ai",
        setup_family="pullback_continuation",
        strategy_spec={},
        tools=["fast_fingers"],
        source="legacy_source",
        signal_rules=[],
        signal_logic="AND",
    )

    merged = _with_strategy_metadata({"ema_fast": 12, "ema_slow": 34}, base)

    assert merged["tools"] == {"fast_fingers": True}
    assert merged["setup_family"] == "momentum_expansion"


def test_with_strategy_metadata_prefers_strategy_spec_over_stale_flat_fields():
    base = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "setup_family": "discretionary_ai",
            "signal_mode": "ai_signal",
            "prompt_bundle": None,
            "metadata": {"source": "ui_ai_signal_card"},
        },
        tools={"session_filter": True},
        source="legacy_flat_source",
    )

    merged = _with_strategy_metadata({"ema_fast": 12, "ema_slow": 34}, base)

    assert merged["signal_mode"] == "ai_signal"
    assert merged["setup_family"] == "discretionary_ai"
    assert merged["source"] == "ui_ai_signal_card"
    assert merged["strategy_spec"]["spec_version"] == "v1"
    assert merged["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_with_strategy_metadata_prefers_strategy_spec_entry_model_rules_and_logic():
    base = BacktestParams(
        signal_mode="algo_ai",
        setup_family="momentum_expansion",
        strategy_spec={
            "setup_family": "momentum_expansion",
            "signal_mode": "algo_ai",
            "entry_model": {
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        },
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
    )

    merged = _with_strategy_metadata({"ema_fast": 12, "ema_slow": 34}, base)

    assert merged["signal_rules"] == [{"source": "macd_crossover"}]
    assert merged["signal_logic"] == "OR"


def test_with_strategy_metadata_preserves_none_signal_rules_for_default_ema_resolution():
    base = BacktestParams.model_construct(
        signal_mode="algo_ai",
        setup_family="",
        strategy_spec={},
        tools={},
        source="legacy_source",
        signal_rules=None,
        signal_logic="AND",
    )

    merged = _with_strategy_metadata({"ema_fast": 12, "ema_slow": 34}, base)

    assert merged["signal_rules"] == [{"source": "ema_crossover"}]
    assert merged["setup_family"] == "trend_continuation"


def test_walk_forward_run_preserves_strategy_metadata_in_best_params(monkeypatch):
    captured_params: list[dict] = []

    def fake_optimize_construction(*args, **kwargs):
        return (
            {
                "ema_fast": 12,
                "ema_slow": 34,
                "tp1_rr": 1.5,
                "tp2_rr": 2.5,
                "sl_min_points": 50.0,
                "sl_max_points": 200.0,
                "sl_buffer_atr": 0.1,
                "confidence_threshold": 0.6,
                "entry_zone_atr_mult": 0.2,
                "rsi_ob": 70,
                "rsi_os": 30,
                "signal_rules": [{"source": "ema_crossover"}],
                "signal_logic": "AND",
            },
            1.0,
            False,
        )

    def fake_run_vectorbt_backtest(df, params, asset_config, **kwargs):
        captured_params.append(dict(params))
        return SimpleNamespace(
            sharpe=1.2,
            trade_count=20,
            win_rate=0.55,
            max_drawdown_pct=10.0,
        )

    monkeypatch.setattr(
        "alphaloop.backtester.optimizer.optimize_construction",
        fake_optimize_construction,
    )
    monkeypatch.setattr(
        "alphaloop.backtester.vbt_engine.run_vectorbt_backtest",
        fake_run_vectorbt_backtest,
    )

    base = BacktestParams(
        signal_mode="ai_signal",
        setup_family="momentum_expansion",
        strategy_spec={"setup_family": "momentum_expansion", "signal_mode": "ai_signal"},
        tools={"news_filter": True},
        source="meta_loop",
    )

    result = WalkForwardEngine(n_trials=1).run(
        _ohlcv(),
        base,
        asset_config=SimpleNamespace(),
        symbol="XAUUSD",
    )

    assert result.best_params["signal_mode"] == "ai_signal"
    assert result.best_params["setup_family"] == "momentum_expansion"
    assert result.best_params["strategy_spec"]["setup_family"] == "momentum_expansion"
    assert result.best_params["tools"] == {"news_filter": True}
    assert result.best_params["source"] == "meta_loop"
    assert len(captured_params) == 2
    assert all(p["setup_family"] == "momentum_expansion" for p in captured_params)


def test_walk_forward_run_prefers_strategy_spec_metadata_in_best_params(monkeypatch):
    captured_params: list[dict] = []

    def fake_optimize_construction(*args, **kwargs):
        return (
            {
                "ema_fast": 12,
                "ema_slow": 34,
                "tp1_rr": 1.5,
                "tp2_rr": 2.5,
                "sl_min_points": 50.0,
                "sl_max_points": 200.0,
                "sl_buffer_atr": 0.1,
                "confidence_threshold": 0.6,
                "entry_zone_atr_mult": 0.2,
                "rsi_ob": 70,
                "rsi_os": 30,
                "signal_rules": [{"source": "ema_crossover"}],
                "signal_logic": "AND",
            },
            1.0,
            False,
        )

    def fake_run_vectorbt_backtest(df, params, asset_config, **kwargs):
        captured_params.append(dict(params))
        return SimpleNamespace(
            sharpe=1.2,
            trade_count=20,
            win_rate=0.55,
            max_drawdown_pct=10.0,
        )

    monkeypatch.setattr(
        "alphaloop.backtester.optimizer.optimize_construction",
        fake_optimize_construction,
    )
    monkeypatch.setattr(
        "alphaloop.backtester.vbt_engine.run_vectorbt_backtest",
        fake_run_vectorbt_backtest,
    )

    base = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "setup_family": "discretionary_ai",
            "signal_mode": "ai_signal",
            "metadata": {"source": "ui_ai_signal_card"},
        },
        tools={"news_filter": True},
        source="legacy_flat_source",
    )

    result = WalkForwardEngine(n_trials=1).run(
        _ohlcv(),
        base,
        asset_config=SimpleNamespace(),
        symbol="XAUUSD",
    )

    assert result.best_params["signal_mode"] == "ai_signal"
    assert result.best_params["setup_family"] == "discretionary_ai"
    assert result.best_params["source"] == "ui_ai_signal_card"
    assert len(captured_params) == 2
    assert all(p["signal_mode"] == "ai_signal" for p in captured_params)
    assert all(p["setup_family"] == "discretionary_ai" for p in captured_params)
    assert all(p["source"] == "ui_ai_signal_card" for p in captured_params)
