from types import SimpleNamespace

from alphaloop.config.assets import get_asset_config
from alphaloop.signals.engine import _build_signal_user_prompt


def test_build_signal_user_prompt_accepts_namespace_session() -> None:
    asset = get_asset_config("XAUUSD")
    context = {
        "current_price": {"bid": 100.0, "ask": 100.2, "spread": 0.2},
        "timeframes": {
            "H1": {"indicators": {"ema_fast": 1, "ema_slow": 2, "ema200": 3, "rsi": 50, "atr": 1.2, "trend_bias": "neutral"}},
            "M15": {"indicators": {"ema_fast_period": 21, "ema_slow_period": 55, "ema_fast": 1, "ema_slow": 2, "rsi": 48, "bos": None, "fvg": None}},
        },
        "session": SimpleNamespace(name="London", score=0.8),
        "dxy": {"value": 104.2},
        "macro_sentiment": {"bias": "neutral"},
    }

    prompt = _build_signal_user_prompt(asset, context, tool_results=[])

    assert "SESSION: London (quality 0.8)" in prompt
    assert "CURRENT PRICE:" in prompt
