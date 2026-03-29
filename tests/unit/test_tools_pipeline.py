"""
Unit tests for tools/pipeline.py — pipeline with mock tools.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from alphaloop.data.market_context import MarketContext, PriceSnapshot, SessionInfo
from alphaloop.tools.base import BaseTool, ToolResult
from alphaloop.tools.pipeline import FilterPipeline


# ── Mock tools ────────────────────────────────────────────────────────────────


class PassTool(BaseTool):
    name = "pass_tool"
    description = "Always passes"

    async def run(self, context) -> ToolResult:
        return ToolResult(passed=True, reason="All good")


class BlockTool(BaseTool):
    name = "block_tool"
    description = "Always blocks"

    async def run(self, context) -> ToolResult:
        return ToolResult(
            passed=False,
            reason="Blocked for testing",
            severity="block",
            size_modifier=0.0,
        )


class WarnTool(BaseTool):
    name = "warn_tool"
    description = "Warns but does not block"

    async def run(self, context) -> ToolResult:
        return ToolResult(
            passed=False,
            reason="Warning issued",
            severity="warn",
            size_modifier=0.8,
        )


class SizeReduceTool(BaseTool):
    name = "size_reduce_tool"
    description = "Reduces size"

    async def run(self, context) -> ToolResult:
        return ToolResult(
            passed=True,
            reason="Reducing size",
            size_modifier=0.5,
        )


class BiasTool(BaseTool):
    name = "bias_tool"
    description = "Returns bullish bias"

    async def run(self, context) -> ToolResult:
        return ToolResult(
            passed=True,
            reason="Bullish signal",
            bias="bullish",
        )


class CrashTool(BaseTool):
    name = "crash_tool"
    description = "Crashes on run"

    async def run(self, context) -> ToolResult:
        raise RuntimeError("Intentional crash")


def _make_context(**kwargs) -> MarketContext:
    """Build a minimal MarketContext for testing."""
    defaults = {
        "symbol": "XAUUSD",
        "trade_direction": "BUY",
        "session": SessionInfo(name="london_ny_overlap", score=1.0, hour_utc=14),
        "price": PriceSnapshot(bid=2000.0, ask=2000.5, spread=0.5),
    }
    defaults.update(kwargs)
    return MarketContext(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestFilterPipeline:
    @pytest.mark.asyncio
    async def test_all_pass(self):
        pipeline = FilterPipeline(tools=[PassTool(), PassTool()])
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is True
        assert result["blocked_by"] is None
        assert result["size_modifier"] == 1.0
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_block_short_circuits(self):
        """Pipeline should stop after first block tool."""
        tools = [PassTool(), BlockTool(), PassTool()]
        pipeline = FilterPipeline(tools=tools, short_circuit=True)
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is False
        assert result["blocked_by"] == "block_tool"
        assert result["block_reason"] == "Blocked for testing"
        # Third tool should NOT have run
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_no_short_circuit(self):
        """Without short_circuit, all tools run even after a block."""
        tools = [PassTool(), BlockTool(), PassTool()]
        pipeline = FilterPipeline(tools=tools, short_circuit=False)
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is False
        assert len(result["results"]) == 3

    @pytest.mark.asyncio
    async def test_warn_does_not_short_circuit(self):
        """Warn severity should NOT trigger short-circuit."""
        tools = [WarnTool(), PassTool()]
        pipeline = FilterPipeline(tools=tools, short_circuit=True)
        result = await pipeline.run(_make_context())
        # Warn doesn't block with severity="block", so pipeline continues
        assert len(result["results"]) == 2

    @pytest.mark.asyncio
    async def test_size_modifier_accumulates(self):
        """Size modifiers should multiply across tools."""
        tools = [SizeReduceTool(), SizeReduceTool()]
        pipeline = FilterPipeline(tools=tools)
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is True
        # 0.5 * 0.5 = 0.25
        assert result["size_modifier"] == 0.25

    @pytest.mark.asyncio
    async def test_size_floor_blocks(self):
        """Pipeline should block if combined size_modifier below floor."""
        tools = [SizeReduceTool(), SizeReduceTool()]
        pipeline = FilterPipeline(tools=tools, size_floor=0.30)
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is False
        assert result["blocked_by"] == "pipeline_size_floor"

    @pytest.mark.asyncio
    async def test_bias_tracked(self):
        """Pipeline should track last non-neutral bias."""
        tools = [PassTool(), BiasTool()]
        pipeline = FilterPipeline(tools=tools)
        result = await pipeline.run(_make_context())
        assert result["bias"] == "bullish"

    @pytest.mark.asyncio
    async def test_crash_results_in_block(self):
        """A crashing tool should produce a fail-safe block."""
        tools = [CrashTool()]
        pipeline = FilterPipeline(tools=tools)
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is False
        assert result["blocked_by"] == "crash_tool"
        assert "fail-safe" in result["block_reason"].lower()

    @pytest.mark.asyncio
    async def test_empty_pipeline_allows(self):
        """Pipeline with no tools should allow the trade."""
        pipeline = FilterPipeline(tools=[])
        result = await pipeline.run(_make_context())
        assert result["allow_trade"] is True

    @pytest.mark.asyncio
    async def test_get_tool(self):
        """get_tool() should find tools by name."""
        pt = PassTool()
        pipeline = FilterPipeline(tools=[pt, BlockTool()])
        assert pipeline.get_tool("pass_tool") is pt
        assert pipeline.get_tool("nonexistent") is None

    @pytest.mark.asyncio
    async def test_results_have_tool_names(self):
        """Each result should carry the tool name."""
        pipeline = FilterPipeline(tools=[PassTool()])
        result = await pipeline.run(_make_context())
        assert result["results"][0]["tool_name"] == "pass_tool"

    @pytest.mark.asyncio
    async def test_results_have_latency(self):
        """Each result should have latency_ms >= 0."""
        pipeline = FilterPipeline(tools=[PassTool()])
        result = await pipeline.run(_make_context())
        assert result["results"][0]["latency_ms"] >= 0
