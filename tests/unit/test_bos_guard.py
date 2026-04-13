"""Unit tests for bos_guard/tool.py — neutral 50.0 when no BOS detected."""

import pytest
from unittest.mock import MagicMock

from alphaloop.tools.plugins.bos_guard.tool import BOSGuard


def _ctx(bos_data=None, direction="BUY"):
    ctx = MagicMock()
    ctx.trade_direction = direction
    if bos_data is None:
        ctx.indicators = {"M15": {}}
    else:
        ctx.indicators = {"M15": {"bos": bos_data}}
    return ctx


def _bos(bullish_bos=False, bearish_bos=False, bull_atr=0.0, bear_atr=0.0):
    return {
        "bullish_bos": bullish_bos,
        "bearish_bos": bearish_bos,
        "bullish_break_atr": bull_atr,
        "bearish_break_atr": bear_atr,
    }


@pytest.mark.asyncio
async def test_no_bos_data_returns_50():
    """When BOS data is unavailable, score should be 50 (neutral)."""
    tool = BOSGuard()
    result = await tool.extract_features(_ctx(bos_data=None))
    assert result.features["bos_strength"] == 50.0


@pytest.mark.asyncio
async def test_no_bull_bos_buy_returns_50():
    """BUY with no bullish BOS (pullback setup) should score 50, not 0."""
    tool = BOSGuard()
    result = await tool.extract_features(_ctx(bos_data=_bos(), direction="BUY"))
    assert result.features["bos_strength"] == 50.0, (
        f"No BOS for BUY should score 50, got {result.features['bos_strength']}"
    )


@pytest.mark.asyncio
async def test_no_bear_bos_sell_returns_50():
    """SELL with no bearish BOS should score 50, not 0."""
    tool = BOSGuard()
    result = await tool.extract_features(_ctx(bos_data=_bos(), direction="SELL"))
    assert result.features["bos_strength"] == 50.0


@pytest.mark.asyncio
async def test_no_bos_no_direction_returns_50():
    """No BOS with no direction should score 50."""
    tool = BOSGuard()
    result = await tool.extract_features(_ctx(bos_data=_bos(), direction=""))
    assert result.features["bos_strength"] == 50.0


@pytest.mark.asyncio
async def test_bullish_bos_05atr_buy_scores_75():
    """BOS with 0.5 ATR break for BUY should score 75 (50 + 0.5*50)."""
    tool = BOSGuard()
    result = await tool.extract_features(
        _ctx(bos_data=_bos(bullish_bos=True, bull_atr=0.5), direction="BUY")
    )
    assert result.features["bos_strength"] == pytest.approx(75.0)


@pytest.mark.asyncio
async def test_bullish_bos_1atr_buy_scores_100():
    """BOS with 1.0 ATR break for BUY should cap at 100."""
    tool = BOSGuard()
    result = await tool.extract_features(
        _ctx(bos_data=_bos(bullish_bos=True, bull_atr=1.0), direction="BUY")
    )
    assert result.features["bos_strength"] == 100.0


@pytest.mark.asyncio
async def test_bearish_bos_sell_scores_above_50():
    """Bearish BOS for SELL should score > 50."""
    tool = BOSGuard()
    result = await tool.extract_features(
        _ctx(bos_data=_bos(bearish_bos=True, bear_atr=0.3), direction="SELL")
    )
    assert result.features["bos_strength"] > 50.0


@pytest.mark.asyncio
async def test_bos_strength_bounded_0_100():
    """bos_strength must stay in [0, 100]."""
    tool = BOSGuard()
    for atr in [0.0, 0.5, 1.0, 2.0, 5.0]:
        result = await tool.extract_features(
            _ctx(bos_data=_bos(bullish_bos=True, bull_atr=atr), direction="BUY")
        )
        score = result.features["bos_strength"]
        assert 0.0 <= score <= 100.0, f"bos_strength={score} out of bounds for atr={atr}"
