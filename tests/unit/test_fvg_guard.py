"""Unit tests for fvg_guard/tool.py — neutral 50.0 when no FVGs present."""

import pytest
from unittest.mock import MagicMock

from alphaloop.tools.plugins.fvg_guard.tool import FVGGuard


def _ctx(fvg_data=None, direction="BUY"):
    ctx = MagicMock()
    ctx.trade_direction = direction
    if fvg_data is None:
        ctx.indicators = {"M15": {}}
    else:
        ctx.indicators = {"M15": {"fvg": fvg_data}}
    return ctx


def _gap(size_atr=0.2, bottom=2740.0, top=2745.0):
    return {"size_atr": size_atr, "bottom": bottom, "top": top, "midpoint": (bottom + top) / 2}


@pytest.mark.asyncio
async def test_no_fvg_data_returns_50():
    """When FVG data unavailable, both features should be 50."""
    tool = FVGGuard()
    result = await tool.extract_features(_ctx(fvg_data=None))
    assert result.features["fvg_presence"] == 50.0
    assert result.features["fvg_quality"] == 50.0


@pytest.mark.asyncio
async def test_no_directional_fvgs_returns_50():
    """No bullish FVGs for BUY → 50 (neutral), not 0 (catastrophic)."""
    tool = FVGGuard()
    result = await tool.extract_features(
        _ctx(fvg_data={"bullish": [], "bearish": [_gap()]}, direction="BUY")
    )
    assert result.features["fvg_presence"] == 50.0, (
        f"No bullish FVGs for BUY should score 50, got {result.features['fvg_presence']}"
    )
    assert result.features["fvg_quality"] == 50.0


@pytest.mark.asyncio
async def test_no_bearish_fvgs_sell_returns_50():
    """No bearish FVGs for SELL → 50."""
    tool = FVGGuard()
    result = await tool.extract_features(
        _ctx(fvg_data={"bullish": [_gap()], "bearish": []}, direction="SELL")
    )
    assert result.features["fvg_presence"] == 50.0
    assert result.features["fvg_quality"] == 50.0


@pytest.mark.asyncio
async def test_one_fvg_presence_above_50():
    """1 bullish FVG for BUY → fvg_presence > 50."""
    tool = FVGGuard()
    result = await tool.extract_features(
        _ctx(fvg_data={"bullish": [_gap(size_atr=0.3)], "bearish": []}, direction="BUY")
    )
    assert result.features["fvg_presence"] > 50.0


@pytest.mark.asyncio
async def test_large_fvg_quality_above_50():
    """FVG with 0.5 ATR → fvg_quality = 100."""
    tool = FVGGuard()
    result = await tool.extract_features(
        _ctx(fvg_data={"bullish": [_gap(size_atr=0.5)], "bearish": []}, direction="BUY")
    )
    assert result.features["fvg_quality"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_features_bounded_0_100():
    """All feature values must stay in [0, 100]."""
    tool = FVGGuard()
    scenarios = [
        {"bullish": [], "bearish": []},
        {"bullish": [_gap(size_atr=0.1)], "bearish": []},
        {"bullish": [_gap(size_atr=0.5), _gap(size_atr=0.3)], "bearish": []},
        {"bullish": [_gap(size_atr=1.0)] * 5, "bearish": []},
    ]
    for data in scenarios:
        result = await tool.extract_features(_ctx(fvg_data=data, direction="BUY"))
        for key, val in result.features.items():
            assert 0.0 <= val <= 100.0, f"{key}={val} out of bounds"
