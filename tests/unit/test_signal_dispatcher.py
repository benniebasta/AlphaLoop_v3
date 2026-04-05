"""Unit tests for trading.signal_dispatcher.SignalDispatcher."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from alphaloop.trading.signal_dispatcher import SignalDispatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dispatcher(**kwargs):
    defaults = dict(
        signal_engine=None,
        ai_caller=None,
        symbol="XAUUSD",
        instance_id="test-1",
    )
    defaults.update(kwargs)
    return SignalDispatcher(**defaults)


def _make_hypothesis(direction="BUY", confidence=0.75):
    h = MagicMock()
    h.direction = direction
    h.confidence = confidence
    return h


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_defaults():
    d = _make_dispatcher()
    assert d.symbol == "XAUUSD"
    assert d.signal_model_id == ""
    assert d._algo_engine is None


# ---------------------------------------------------------------------------
# update_algo_engine / update_signal_model
# ---------------------------------------------------------------------------

def test_update_algo_engine():
    d = _make_dispatcher()
    engine = MagicMock()
    d.update_algo_engine(engine)
    assert d._algo_engine is engine


def test_update_signal_model():
    d = _make_dispatcher()
    d.update_signal_model("gemini-2.5-pro")
    assert d.signal_model_id == "gemini-2.5-pro"


def test_update_algo_engine_then_update_again():
    d = _make_dispatcher()
    e1, e2 = MagicMock(), MagicMock()
    d.update_algo_engine(e1)
    d.update_algo_engine(e2)
    assert d._algo_engine is e2


# ---------------------------------------------------------------------------
# AI signal mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_ai_signal_mode_returns_hypothesis():
    hyp = _make_hypothesis()
    signal_engine = MagicMock()
    signal_engine.generate_hypothesis = AsyncMock(return_value=hyp)
    ai_caller = MagicMock()

    d = _make_dispatcher(signal_engine=signal_engine, ai_caller=ai_caller)
    d.update_signal_model("model-x")

    result = await d.dispatch(
        MagicMock(), MagicMock(),
        signal_mode="ai_signal",
        active_strategy=None,
    )

    assert result is hyp
    signal_engine.generate_hypothesis.assert_awaited_once()
    call_kwargs = signal_engine.generate_hypothesis.call_args
    assert call_kwargs.kwargs["model_id"] == "model-x"
    assert call_kwargs.kwargs["ai_caller"] is ai_caller


@pytest.mark.asyncio
async def test_dispatch_ai_signal_no_engine_returns_none():
    d = _make_dispatcher(signal_engine=None)
    result = await d.dispatch(MagicMock(), None, signal_mode="ai_signal")
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_ai_signal_engine_exception_returns_none():
    signal_engine = MagicMock()
    signal_engine.generate_hypothesis = AsyncMock(side_effect=RuntimeError("LLM down"))
    d = _make_dispatcher(signal_engine=signal_engine)

    result = await d.dispatch(MagicMock(), None, signal_mode="ai_signal")
    assert result is None


# ---------------------------------------------------------------------------
# Algo mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_algo_only_uses_algo_engine():
    hyp = _make_hypothesis(direction="SELL")
    algo = MagicMock()
    algo.generate_hypothesis = AsyncMock(return_value=hyp)

    d = _make_dispatcher()
    d.update_algo_engine(algo)

    result = await d.dispatch(MagicMock(), None, signal_mode="algo_only")
    assert result is hyp
    algo.generate_hypothesis.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_algo_ai_mode_uses_algo_engine():
    hyp = _make_hypothesis()
    algo = MagicMock()
    algo.generate_hypothesis = AsyncMock(return_value=hyp)

    # algo_ai with no signal_engine — should still use algo engine
    d = _make_dispatcher(signal_engine=None)
    d.update_algo_engine(algo)

    result = await d.dispatch(MagicMock(), None, signal_mode="algo_ai")
    assert result is hyp


@pytest.mark.asyncio
async def test_dispatch_algo_engine_none_returns_none():
    d = _make_dispatcher()
    result = await d.dispatch(MagicMock(), None, signal_mode="algo_only")
    assert result is None


@pytest.mark.asyncio
async def test_dispatch_algo_engine_exception_returns_none():
    algo = MagicMock()
    algo.generate_hypothesis = AsyncMock(side_effect=ValueError("bad candle"))
    d = _make_dispatcher()
    d.update_algo_engine(algo)

    result = await d.dispatch(MagicMock(), None, signal_mode="algo_only")
    assert result is None


# ---------------------------------------------------------------------------
# AI mode takes precedence over algo engine when signal_engine is set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_signal_mode_does_not_call_algo_engine():
    hyp = _make_hypothesis()
    signal_engine = MagicMock()
    signal_engine.generate_hypothesis = AsyncMock(return_value=hyp)
    algo = MagicMock()
    algo.generate_hypothesis = AsyncMock(return_value=_make_hypothesis())

    d = _make_dispatcher(signal_engine=signal_engine)
    d.update_algo_engine(algo)

    await d.dispatch(MagicMock(), None, signal_mode="ai_signal")

    signal_engine.generate_hypothesis.assert_awaited_once()
    algo.generate_hypothesis.assert_not_awaited()


# ---------------------------------------------------------------------------
# Hypothesis returned is not modified
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_passes_through_hypothesis_unmodified():
    hyp = _make_hypothesis(direction="SELL", confidence=0.91)
    signal_engine = MagicMock()
    signal_engine.generate_hypothesis = AsyncMock(return_value=hyp)

    d = _make_dispatcher(signal_engine=signal_engine)
    result = await d.dispatch(MagicMock(), None, signal_mode="ai_signal")

    assert result.direction == "SELL"
    assert result.confidence == 0.91
