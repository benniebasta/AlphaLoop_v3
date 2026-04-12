from alphaloop.backtester.comparison import (
    _comparison_backtest_params,
    _resolve_comparison_signal_sources,
    _resolve_comparison_setup_type,
)
from alphaloop.backtester.params import BacktestParams


def test_comparison_backtest_params_default_identity_follows_filters():
    params = _comparison_backtest_params(None, ["fast_fingers"])

    assert params.signal_mode == "algo_ai"
    assert params.setup_family == "momentum_expansion"
    assert params.source == "comparison"
    assert params.tools == {"fast_fingers": True}
    assert params.strategy_spec["setup_family"] == "momentum_expansion"
    assert params.strategy_spec["metadata"]["source"] == "comparison"


def test_comparison_backtest_params_preserve_explicit_params_instance():
    existing = BacktestParams(signal_mode="algo_ai", setup_family="range_reversal", source="custom")

    params = _comparison_backtest_params(existing, ["fast_fingers"])

    assert params is existing


def test_resolve_comparison_setup_type_prefers_strategy_spec_family():
    params = BacktestParams(
        signal_mode="algo_ai",
        setup_family="pullback_continuation",
        strategy_spec={
            "signal_mode": "algo_ai",
            "setup_family": "momentum_expansion",
        },
        signal_rules=[{"source": "ema_crossover"}],
    )

    setup_type = _resolve_comparison_setup_type(params, "ema_crossover_macd_crossover")

    assert setup_type == "continuation"


def test_resolve_comparison_setup_type_falls_back_to_source_mapping():
    params = BacktestParams(
        signal_mode="algo_ai",
        setup_family="",
        strategy_spec={},
        signal_rules=[{"source": "ema_crossover"}],
    )

    setup_type = _resolve_comparison_setup_type(params, "bos_confirm")

    assert setup_type == "breakout"


def test_resolve_comparison_signal_sources_prefers_spec_first_rule_sources():
    params = BacktestParams(
        signal_mode="algo_ai",
        signal_rules=[{"source": "ema_crossover"}],
        strategy_spec={
            "signal_mode": "algo_ai",
            "entry_model": {
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        },
    )

    signal_sources = _resolve_comparison_signal_sources(params, "ema_crossover_macd_crossover")

    assert signal_sources == ["macd_crossover"]


def test_resolve_comparison_signal_sources_keeps_exact_source_label_when_available():
    params = BacktestParams(signal_mode="algo_ai")

    signal_sources = _resolve_comparison_signal_sources(params, "bos_confirm")

    assert signal_sources == ["bos_confirm"]
