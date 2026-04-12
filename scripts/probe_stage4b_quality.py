"""Gate-2 verification probe.

Runs ``StructuralQuality.evaluate()`` once with the OLD ``STAGE_TOOL_MAP``
and once with the NEW one, on an identical realistic MarketContext, and
prints the delta side-by-side.

Purpose: demonstrate that the Stage 4B fix changes the actual numbers the
quality scorer produces, without needing to start the full trading loop
(which requires MT5 connectivity) or wait for a fresh cycle.

Usage::

    python -m scripts.probe_stage4b_quality
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from alphaloop.pipeline.quality import StructuralQuality
from alphaloop.scoring.weights import SCORING_GROUPS
from alphaloop.tools.registry import ToolRegistry


STRATEGY_PATH = Path("strategy_versions/phantom-knight-BTCUSD_ai_v1.json")


# The pre-Gate-2 list — exactly what was in registry.py before commit 33bcd49.
OLD_QUALITY_LIST = [
    "ema200_filter",
    "alma_filter",
    "bollinger_filter",
    "volume_filter",
    "dxy_filter",
    "sentiment_filter",
]

# The post-Gate-2 list — same as what registry.py now uses.
NEW_QUALITY_LIST = [
    # trend
    "ema200_filter", "alma_filter", "dxy_filter", "ema_crossover",
    # momentum
    "macd_filter", "adx_filter", "rsi_feature", "bollinger_filter", "fast_fingers",
    # structure
    "bos_guard", "fvg_guard", "swing_structure",
    # volume
    "volume_filter", "sentiment_filter",
    # volatility
    "choppiness_index", "news_filter", "volatility_filter", "liq_vacuum_guard",
]


def _make_rich_context(direction: str = "BUY") -> MagicMock:
    """Realistic MarketContext — same shape as the live loop produces.

    Uses dict-shaped ``dxy``/``sentiment``/``news`` and nested dict
    ``choppiness`` so plugin ``.get()`` calls succeed. Mirrors the minimum
    fields every quality-stage tool needs to compute a real score.
    """
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
            # choppiness_index expects a dict-shaped value with at least a 'value' key
            "choppiness": {"value": 32.0, "regime": "trending"},
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
        bid=3100.5, ask=3101.0, spread=1.5, time=datetime.now(timezone.utc)
    )
    # dxy and sentiment are dict-shaped in production (live_feed writes them
    # as JSON blobs read from the DB). Use dicts here, not SimpleNamespaces.
    ctx.news = []
    ctx.dxy = {"value": 103.5, "direction": "neutral", "score": 0.5, "change_pct": 0.1}
    ctx.sentiment = {"score": 0.1, "direction": "neutral", "source": "polymarket"}
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


def _strategy_enabled_tools() -> dict[str, bool]:
    """Read the active strategy's ``tools`` dict so we can mimic real
    enablement filtering when comparing OLD vs NEW."""
    if not STRATEGY_PATH.exists():
        return {}
    return json.loads(STRATEGY_PATH.read_text()).get("tools") or {}


def _get_tools_for(name_list: list[str], registry: ToolRegistry, enabled_filter: dict[str, bool] | None = None) -> list:
    """Return tool instances, optionally filtered by strategy enablement."""
    tools: list = []
    for name in name_list:
        if enabled_filter is not None and not enabled_filter.get(name, False):
            continue
        tool = registry.get_tool(name)
        if tool is not None:
            tools.append(tool)
    return tools


async def _run_evaluate(
    tool_names: list[str],
    registry: ToolRegistry,
    direction: str,
    enabled_filter: dict[str, bool] | None = None,
) -> dict:
    tools = _get_tools_for(tool_names, registry, enabled_filter)
    quality = StructuralQuality(tools=tools)
    ctx = _make_rich_context(direction=direction)
    result = await quality.evaluate(ctx)
    return {
        "overall": result.overall_score,
        "max": result.max_score,
        "low_count": result.low_score_count,
        "groups": dict(result.group_scores),
        "tools_run": len(tools),
        "tool_names": [t.name for t in tools],
    }


async def _main() -> None:
    registry = ToolRegistry()
    print(f"ToolRegistry loaded {len(registry._instances)} plugins")
    enabled = _strategy_enabled_tools()
    print(f"Strategy enabled tools: {sum(1 for v in enabled.values() if v)}/{len(enabled)}")
    print()

    for direction in ("BUY", "SELL"):
        print(f"=== direction={direction} ===")

        # Apples-to-apples: apply the strategy's enablement filter to BOTH lists.
        old = await _run_evaluate(OLD_QUALITY_LIST, registry, direction, enabled)
        new = await _run_evaluate(NEW_QUALITY_LIST, registry, direction, enabled)

        print(f"OLD strategy-filtered tools ({old['tools_run']}): {old['tool_names']}")
        print(f"NEW strategy-filtered tools ({new['tools_run']}): {new['tool_names']}")
        print()
        print(f"{'metric':14}{'OLD (pre-Gate-2)':>22}{'NEW (post-Gate-2)':>22}{'delta':>12}")
        print("-" * 70)
        print(f"{'tools_run':14}{old['tools_run']:>22}{new['tools_run']:>22}{new['tools_run'] - old['tools_run']:>+12}")
        print(f"{'overall':14}{old['overall']:>22.2f}{new['overall']:>22.2f}{new['overall'] - old['overall']:>+12.2f}")
        print(f"{'max':14}{old['max']:>22.2f}{new['max']:>22.2f}{new['max'] - old['max']:>+12.2f}")
        print(f"{'low_count':14}{old['low_count']:>22}{new['low_count']:>22}{new['low_count'] - old['low_count']:>+12}")
        print()
        print("group_scores:")
        for g in SCORING_GROUPS:
            o = old["groups"].get(g, 50.0)
            n = new["groups"].get(g, 50.0)
            marker = "  (was dead)" if abs(o - 50.0) < 0.01 and abs(n - 50.0) > 0.01 else ""
            print(f"  {g:12}{o:>22.2f}{n:>22.2f}{n - o:>+12.2f}{marker}")
        print()


if __name__ == "__main__":
    asyncio.run(_main())
