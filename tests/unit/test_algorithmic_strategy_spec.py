from __future__ import annotations

from alphaloop.signals.algorithmic import (
    AlgorithmicSignalEngine,
    _configured_signal_logic,
    _configured_signal_rules,
)


def _context() -> dict:
    return {
        "current_price": {"bid": 2310.0},
        "timeframes": {
            "M15": {
                "indicators": {
                    "atr": 5.0,
                    "ema_fast": 2312.0,
                    "ema_slow": 2308.0,
                    "rsi": 58.0,
                    "macd_histogram": 0.4,
                    "bb_pct_b": 0.7,
                    "adx": 24.0,
                    "plus_di": 28.0,
                    "minus_di": 16.0,
                }
            }
        },
    }


async def test_algorithmic_hypothesis_uses_strategy_setup_tag():
    engine = AlgorithmicSignalEngine(
        "XAUUSD",
        {"signal_rules": [{"source": "ema_crossover"}], "signal_logic": "AND"},
        prev_ema_state={"fast": 2307.0, "slow": 2309.0},
        setup_tag="momentum_expansion",
    )

    hyp = await engine.generate_hypothesis(_context())

    assert hyp is not None
    assert hyp.setup_tag == "continuation"


async def test_algorithmic_hypothesis_defaults_missing_signal_rules_for_backward_compat():
    engine = AlgorithmicSignalEngine(
        "XAUUSD",
        {"signal_rules": None, "signal_logic": None},
        prev_ema_state={"fast": 2307.0, "slow": 2309.0},
        setup_tag="momentum_expansion",
    )

    hyp = await engine.generate_hypothesis(_context())

    assert hyp is not None
    assert hyp.setup_tag == "continuation"


async def test_algorithmic_hypothesis_fails_closed_on_malformed_signal_rules():
    engine = AlgorithmicSignalEngine(
        "XAUUSD",
        {"signal_rules": "ema_crossover", "signal_logic": "OR"},
        prev_ema_state={"fast": 2307.0, "slow": 2309.0},
        setup_tag="momentum_expansion",
    )

    hyp = await engine.generate_hypothesis(_context())

    assert hyp is None


def test_algorithmic_rule_loader_prefers_strategy_spec_entry_model():
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
