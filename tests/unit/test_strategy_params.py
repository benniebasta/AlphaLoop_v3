"""Tests for strategy parameters."""

from alphaloop.config.strategy_params import StrategyParams, validate_strategy_params


def test_default_params():
    p = StrategyParams()
    assert p.sl_atr_mult == 1.5
    assert p.tp1_rr == 1.5
    assert p.ema_fast == 21


def test_validate_clamps_oob():
    params = {"params": {"rsi_overbought": 95.0, "tp1_rr": 0.5}}
    result = validate_strategy_params(params)
    assert result["params"]["rsi_overbought"] == 90.0  # clamped to max
    assert result["params"]["tp1_rr"] == 1.0  # clamped to min


def test_validate_leaves_valid_unchanged():
    params = {"params": {"rsi_overbought": 75.0}}
    result = validate_strategy_params(params)
    assert result["params"]["rsi_overbought"] == 75.0
