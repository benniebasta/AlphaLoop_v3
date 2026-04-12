"""End-to-end smoke test for Gate-2 Stage 4B quality scoring.

Proves that after expanding ``STAGE_TOOL_MAP["quality"]``, running
``StructuralQuality.evaluate()`` with the registry's quality-stage tools on a
realistic market context produces group_scores that are NOT stuck at the
neutral default of 50.0 for ``structure``, ``volatility`` or ``momentum``.

This complements ``tests/unit/test_stage_tool_map_coverage.py`` which
enforces the static-structure invariant. This test exercises the dynamic
path and would catch a regression where a tool silently fails
``extract_features()`` and falls back to neutral.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from alphaloop.pipeline.quality import StructuralQuality
from alphaloop.scoring.weights import SCORING_GROUPS
from alphaloop.tools.registry import STAGE_TOOL_MAP, ToolRegistry


def _make_rich_context(direction: str = "BUY") -> MagicMock:
    """Realistic MarketContext with every field a quality-stage tool may read."""
    ctx = MagicMock()
    ctx.symbol = "XAUUSD"
    ctx.trade_direction = direction
    ctx.pip_size = 0.01

    ctx.indicators = {
        "M15": {
            "ema200": 3090.0,
            "ema_fast": 3098.0,
            "ema_slow": 3094.0,
            "alma": 3096.0,
            "atr": 10.0,
            "adx": 35.0,
            "choppiness": 32.0,
            "macd": 1.5,
            "macd_signal": 0.8,
            "macd_hist": 0.7,
            "rsi": 62.0,
            "bb_upper": 3110.0,
            "bb_middle": 3100.0,
            "bb_lower": 3090.0,
            "bb_pct_b": 0.72,
            "bollinger_position": 0.72,
            "volume": 1500.0,
            "volume_ma": 1000.0,
            "volume_ratio": 1.5,
            "bos": {
                "bullish_bos": True,
                "bullish_break_atr": 0.5,
                "bearish_bos": False,
                "bearish_break_atr": 0.0,
                "swing_high": 3108.0,
                "swing_low": 3085.0,
            },
            "fvg": {
                "bullish": [{"size_atr": 0.35, "bottom": 3092.0, "top": 3095.0, "midpoint": 3093.5}],
                "bearish": [],
            },
            "vwap": 3095.0,
            "swing_structure": "bullish",
            "fast_fingers": {
                "is_exhausted_up": False,
                "is_exhausted_down": False,
                "exhaustion_score": 20,
            },
            "tick_jump_atr": 0.2,
            "liq_vacuum": {"bar_range_atr": 0.8, "body_pct": 55},
            "median_spread": 1.5,
        },
        "H1": {
            "atr_pct": 0.003,
            "ema200": 3085.0,
            "ema_fast": 3095.0,
            "ema_slow": 3090.0,
        },
    }

    ctx.session = MagicMock(is_weekend=False, score=0.85, name="london")
    ctx.price = MagicMock(
        bid=3100.5,
        ask=3101.0,
        spread=1.5,
        time=datetime.now(timezone.utc),
    )
    ctx.news = []
    ctx.dxy = SimpleNamespace(value=103.5, direction="neutral")
    ctx.sentiment = SimpleNamespace(score=0.1, direction="neutral")
    ctx.open_trades = {}
    ctx.risk_monitor = SimpleNamespace(
        kill_switch_active=False,
        _kill_switch_active=False,
        _open_risk_usd=0,
        account_balance=10000,
        can_open_trade=AsyncMock(return_value=(True, "")),
    )
    ctx.df = MagicMock(__len__=lambda self: 500)
    ctx.tool_results = []
    return ctx


def _get_quality_stage_tools() -> list:
    """Return the tool instances that STAGE_TOOL_MAP assigns to 'quality'."""
    registry = ToolRegistry()
    names = STAGE_TOOL_MAP.get("quality", [])
    tools: list = []
    for name in names:
        tool = registry.get_tool(name)
        if tool is not None:
            tools.append(tool)
    return tools


@pytest.mark.asyncio
async def test_quality_stage_evaluate_runs_without_crash_on_rich_context():
    """The expanded tool list must not introduce any crash when Stage 4B
    runs ``extract_features()`` on a realistic context. A silent exception
    would log a warning and fall back to the neutral default — exactly the
    regression we're trying to prevent.
    """
    tools = _get_quality_stage_tools()
    quality = StructuralQuality(tools=tools)
    ctx = _make_rich_context(direction="BUY")

    result = await quality.evaluate(ctx)

    assert result is not None
    assert isinstance(result.group_scores, dict)
    # Every canonical scoring group must be present in the output.
    for group in SCORING_GROUPS:
        assert group in result.group_scores, (
            f"Stage 4B returned no score for group {group!r}. "
            f"Got: {result.group_scores}"
        )
    # At least one group must not be the neutral 50.0 — if every group is
    # exactly 50.0, the tool scoring path is silently broken across the board.
    non_neutral = [
        g for g, v in result.group_scores.items() if abs(v - 50.0) > 0.01
    ]
    assert non_neutral, (
        f"All scoring groups returned neutral 50.0. Quality scoring is "
        f"structurally broken. Group scores: {result.group_scores}, "
        f"overall={result.overall_score}"
    )
