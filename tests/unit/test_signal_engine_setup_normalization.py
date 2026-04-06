from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.signals.engine import MultiAssetSignalEngine


@pytest.mark.asyncio
async def test_ai_signal_hypothesis_normalizes_legacy_setup_aliases():
    ai_caller = SimpleNamespace(
        call_model=AsyncMock(
            return_value='{"trend":"bullish","confidence":0.82,"setup":"range","reasoning":"Structured range reversal setup with clear support."}'
        )
    )
    engine = MultiAssetSignalEngine(symbol="XAUUSD")

    context = {
        "symbol": "XAUUSD",
        "current_price": {"bid": 2300.0, "ask": 2300.5},
        "timeframes": {"H1": {"indicators": {}}, "M15": {"indicators": {}}},
        "upcoming_news": [],
        "macro_sentiment": {"bias": "neutral"},
    }

    hypothesis = await engine.generate_hypothesis(
        context,
        ai_caller=ai_caller,
        model_id="test-model",
        prompt_instructions="Prefer range setups.",
    )

    assert hypothesis is not None
    assert hypothesis.setup_tag == "range_bounce"
