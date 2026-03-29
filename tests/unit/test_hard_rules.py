"""Tests for hard rule validation."""

from alphaloop.signals.schema import TradeSignal
from alphaloop.validation.rules import HardRuleChecker


def _make_signal(**overrides) -> TradeSignal:
    base = {
        "trend": "bullish",
        "setup": "pullback",
        "entry_zone": [2340.0, 2342.0],
        "stop_loss": 2320.0,  # 200 pts distance (> sl_min_points 150)
        "take_profit": [2375.0, 2390.0],  # Good RR ratio
        "confidence": 0.85,
        "reasoning": "Strong pullback to EMA21 with bullish structure intact",
    }
    base.update(overrides)
    return TradeSignal(**base)


def _make_context(**overrides) -> dict:
    base = {
        "session": {"name": "london_session", "score": 0.85},
        "current_price": {"bid": 2341.0, "spread": 3.0},
        "timeframes": {
            "H1": {"indicators": {"rsi": 55.0, "ema200": 2300.0, "last_close": 2341.0}},
        },
        "upcoming_news": [],
    }
    base.update(overrides)
    return base


def test_all_rules_pass():
    checker = HardRuleChecker(symbol="XAUUSD")
    signal = _make_signal()
    context = _make_context()
    failures = checker.check(signal, context)
    assert len(failures) == 0


def test_low_confidence_fails():
    checker = HardRuleChecker(symbol="XAUUSD")
    signal = _make_signal(confidence=0.50)
    context = _make_context()
    failures = checker.check(signal, context, cfg={"min_confidence": 0.70})
    assert any("confidence" in f.lower() for f in failures)


def test_sl_wrong_side_fails():
    checker = HardRuleChecker(symbol="XAUUSD")
    # BUY with SL above entry
    signal = _make_signal(stop_loss=2345.0, take_profit=[2375.0])
    context = _make_context()
    failures = checker.check(signal, context)
    assert any("sl" in f.lower() or "buy" in f.lower() for f in failures)


def test_low_rr_fails():
    checker = HardRuleChecker(symbol="XAUUSD")
    # Very tight TP close to entry — but SL still needs good distance
    signal = _make_signal(take_profit=[2345.0])
    context = _make_context()
    failures = checker.check(signal, context, cfg={"min_rr": 1.5})
    assert any("r:r" in f.lower() or "rr" in f.lower() for f in failures)


def test_weekend_session_fails():
    checker = HardRuleChecker(symbol="XAUUSD")
    signal = _make_signal()
    context = _make_context(session={"name": "weekend", "score": 0.0})
    failures = checker.check(signal, context)
    assert any("weekend" in f.lower() for f in failures)


def test_rsi_overbought_buy_fails():
    checker = HardRuleChecker(symbol="XAUUSD")
    signal = _make_signal()
    context = _make_context()
    context["timeframes"]["H1"]["indicators"]["rsi"] = 80.0
    failures = checker.check(signal, context, cfg={"check_rsi": True, "rsi_ob": 75.0})
    assert any("rsi" in f.lower() for f in failures)


def test_ema200_misalignment_fails():
    checker = HardRuleChecker(symbol="XAUUSD")
    signal = _make_signal()  # BUY signal
    context = _make_context()
    context["timeframes"]["H1"]["indicators"]["ema200"] = 2400.0  # price below EMA200
    context["current_price"]["bid"] = 2341.0
    failures = checker.check(signal, context)
    assert any("ema200" in f.lower() for f in failures)
