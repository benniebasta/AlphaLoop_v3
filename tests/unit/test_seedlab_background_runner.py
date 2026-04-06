from alphaloop.seedlab.background_runner import _seedlab_backtest_params


def test_seedlab_backtest_params_infer_family_from_filters():
    params = _seedlab_backtest_params(["fast_fingers"])

    assert params.signal_mode == "algo_ai"
    assert params.setup_family == "momentum_expansion"
    assert params.source == "seedlab"
    assert params.tools == {"fast_fingers": True}
    assert params.signal_rules == [{"source": "ema_crossover"}]
    assert params.strategy_spec["setup_family"] == "momentum_expansion"
    assert params.strategy_spec["metadata"]["source"] == "seedlab"


def test_seedlab_backtest_params_infer_breakout_family_from_filters():
    params = _seedlab_backtest_params(["bos_guard"])

    assert params.setup_family == "breakout_retest"
    assert params.source == "seedlab"
    assert params.strategy_spec["setup_family"] == "breakout_retest"
