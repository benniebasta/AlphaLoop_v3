"""Unit tests for rsi_feature/tool.py — direction-aware rsi_quality scoring."""

import pytest
from unittest.mock import MagicMock

from alphaloop.tools.plugins.rsi_feature.tool import RSIFeature


def _ctx(rsi=None, direction="BUY"):
    ctx = MagicMock()
    ctx.indicators = {"M15": {"rsi": rsi} if rsi is not None else {}}
    ctx.trade_direction = direction
    return ctx


@pytest.mark.asyncio
async def test_rsi_unavailable_returns_50():
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=None))
    assert result.features["rsi_quality"] == 50.0


@pytest.mark.asyncio
async def test_rsi_50_buy_scores_high():
    """RSI=50 for BUY was 16.7 (false contradiction) — now should be 80."""
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=50.0, direction="BUY"))
    assert result.features["rsi_quality"] >= 75.0, (
        f"RSI=50 BUY should score ≥75, got {result.features['rsi_quality']}"
    )


@pytest.mark.asyncio
async def test_rsi_55_buy_scores_high():
    """RSI=55 for BUY was 21.7 (false contradiction) — now should be ~85."""
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=55.0, direction="BUY"))
    assert result.features["rsi_quality"] >= 80.0, (
        f"RSI=55 BUY should score ≥80, got {result.features['rsi_quality']}"
    )


@pytest.mark.asyncio
async def test_rsi_75_buy_overbought_scores_low():
    """RSI=75 for BUY is overbought — should score ≤30 (borderline zone)."""
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=75.0, direction="BUY"))
    assert result.features["rsi_quality"] <= 30.0, (
        f"RSI=75 BUY (overbought) should score ≤30, got {result.features['rsi_quality']}"
    )


@pytest.mark.asyncio
async def test_rsi_50_sell_scores_high():
    """RSI=50 for SELL was 16.7 — now should be ≥75."""
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=50.0, direction="SELL"))
    assert result.features["rsi_quality"] >= 75.0


@pytest.mark.asyncio
async def test_rsi_25_sell_oversold_scores_low():
    """RSI=25 for SELL is oversold — should score ≤30 (borderline zone)."""
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=25.0, direction="SELL"))
    assert result.features["rsi_quality"] <= 30.0


@pytest.mark.asyncio
async def test_rsi_50_no_direction_returns_50():
    """No direction = neutral = 50."""
    tool = RSIFeature()
    result = await tool.extract_features(_ctx(rsi=50.0, direction=""))
    assert result.features["rsi_quality"] == 50.0


@pytest.mark.asyncio
async def test_rsi_quality_bounded_0_100():
    """All outputs must stay within [0, 100]."""
    tool = RSIFeature()
    for rsi in [0, 10, 25, 30, 40, 50, 60, 65, 70, 75, 80, 90, 100]:
        for direction in ["BUY", "SELL", ""]:
            result = await tool.extract_features(_ctx(rsi=float(rsi), direction=direction))
            score = result.features["rsi_quality"]
            assert 0.0 <= score <= 100.0, (
                f"rsi_quality={score} out of bounds for RSI={rsi} direction={direction}"
            )
