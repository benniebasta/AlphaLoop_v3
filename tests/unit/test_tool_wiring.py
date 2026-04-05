"""
tests/unit/test_tool_wiring.py

Verifies that each pipeline stage calls its assigned tool plugins and
respects the strategy card toggle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(name: str, *, passed: bool = True, severity: str = "warn") -> MagicMock:
    """Return a mock BaseTool with timed_run returning a ToolResult."""
    tool = MagicMock()
    tool.name = name
    result = SimpleNamespace(
        tool_name=name,
        passed=passed,
        reason=f"{name} reason",
        severity=severity,
        size_modifier=1.0,
        bias="neutral",
    )
    tool.timed_run = AsyncMock(return_value=result)
    return tool


def _make_context(**kwargs):
    ctx = SimpleNamespace(
        session=None,
        risk_monitor=None,
        news=[],
        price=SimpleNamespace(bid=2750.0, ask=2750.5, spread=0.5, time=None),
        df=list(range(200)),
        indicators={
            "H1": {"atr_pct": 0.003},
            "M15": {"atr": 10.0, "median_spread": 0.3},
        },
        symbol="XAUUSD",
        trade_direction="",
        tool_results=None,
        **kwargs,
    )
    return ctx


# ---------------------------------------------------------------------------
# Stage 1: MarketGate
# ---------------------------------------------------------------------------

class TestMarketGateTools:

    @pytest.mark.asyncio
    async def test_passing_tool_does_not_block(self):
        from alphaloop.pipeline.market_gate import MarketGate
        tool = _make_tool("session_filter", passed=True)
        gate = MarketGate(tools=[tool])
        result = await gate.check(_make_context())
        assert result.tradeable is True
        tool.timed_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_blocking_tool_stops_cycle(self):
        from alphaloop.pipeline.market_gate import MarketGate
        tool = _make_tool("session_filter", passed=False, severity="block")
        gate = MarketGate(tools=[tool])
        result = await gate.check(_make_context())
        assert result.tradeable is False
        assert result.blocked_by == "session_filter"

    @pytest.mark.asyncio
    async def test_no_tools_still_passes(self):
        from alphaloop.pipeline.market_gate import MarketGate
        gate = MarketGate(tools=[])
        result = await gate.check(_make_context())
        assert result.tradeable is True

    @pytest.mark.asyncio
    async def test_toggled_off_tool_not_injected(self):
        """If strategy card disables session_filter it should not be in tools list."""
        from alphaloop.tools.registry import STAGE_TOOL_MAP
        # STAGE_TOOL_MAP must include session_filter under market_gate
        assert "session_filter" in STAGE_TOOL_MAP["market_gate"]


# ---------------------------------------------------------------------------
# Stage 2: RegimeClassifier
# ---------------------------------------------------------------------------

class TestRegimeTools:

    @pytest.mark.asyncio
    async def test_regime_tools_called_never_block(self):
        from alphaloop.pipeline.regime import RegimeClassifier
        tool = _make_tool("adx_filter", passed=False, severity="warn")
        regime = RegimeClassifier(tools=[tool])
        snapshot = await regime.classify(_make_context())
        # Tools must be called but cannot block — snapshot still returned
        tool.timed_run.assert_called_once()
        assert snapshot is not None
        assert snapshot.regime in ("trending", "ranging", "volatile", "neutral")


# ---------------------------------------------------------------------------
# Stage 4A: StructuralInvalidator
# ---------------------------------------------------------------------------

class TestInvalidationTools:

    def _make_signal(self):
        from alphaloop.pipeline.types import CandidateSignal
        return CandidateSignal(
            direction="BUY",
            setup_type="pullback",
            entry_zone=(2749.0, 2751.0),
            stop_loss=2730.0,
            take_profit=[2780.0, 2800.0],
            raw_confidence=0.75,
            rr_ratio=1.6,
        )

    def _make_regime(self):
        from alphaloop.pipeline.types import RegimeSnapshot
        return RegimeSnapshot(
            regime="trending",
            macro_regime="neutral",
            volatility_band="normal",
            allowed_setups=["pullback", "breakout", "continuation"],
        )

    @pytest.mark.asyncio
    async def test_blocking_tool_hard_invalidates(self):
        from alphaloop.pipeline.invalidation import StructuralInvalidator
        tool = _make_tool("liq_vacuum_guard", passed=False, severity="block")
        inv = StructuralInvalidator(tools=[tool])
        result = await inv.validate(self._make_signal(), self._make_regime(), _make_context())
        tool.timed_run.assert_called_once()
        hard_failures = [f for f in result.failures if f.severity == "HARD_INVALIDATE"]
        assert any(f.check_name == "liq_vacuum_guard" for f in hard_failures)

    @pytest.mark.asyncio
    async def test_warn_tool_soft_invalidates(self):
        from alphaloop.pipeline.invalidation import StructuralInvalidator
        tool = _make_tool("vwap_guard", passed=False, severity="warn")
        inv = StructuralInvalidator(tools=[tool])
        result = await inv.validate(self._make_signal(), self._make_regime(), _make_context())
        soft_failures = [f for f in result.failures if f.severity == "SOFT_INVALIDATE"]
        assert any(f.check_name == "vwap_guard" for f in soft_failures)

    @pytest.mark.asyncio
    async def test_passing_tool_no_failure(self):
        from alphaloop.pipeline.invalidation import StructuralInvalidator
        tool = _make_tool("liq_vacuum_guard", passed=True)
        inv = StructuralInvalidator(tools=[tool])
        result = await inv.validate(self._make_signal(), self._make_regime(), _make_context())
        assert not any(f.check_name == "liq_vacuum_guard" for f in result.failures)


# ---------------------------------------------------------------------------
# Stage 7: RiskGate
# ---------------------------------------------------------------------------

class TestRiskGateTools:

    def _make_signal(self):
        from alphaloop.pipeline.types import CandidateSignal
        return CandidateSignal(
            direction="BUY",
            setup_type="pullback",
            entry_zone=(2749.0, 2751.0),
            stop_loss=2730.0,
            take_profit=[2780.0],
            raw_confidence=0.75,
            rr_ratio=1.6,
        )

    @pytest.mark.asyncio
    async def test_risk_filter_block_prevents_trade(self):
        from alphaloop.pipeline.risk_gate import RiskGateRunner
        tool = _make_tool("risk_filter", passed=False, severity="block")
        rg = RiskGateRunner(risk_filter_tool=tool)
        result = await rg.check(self._make_signal(), _make_context())
        assert result.allowed is False
        assert "Risk filter" in result.block_reason
        tool.timed_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_passing_risk_filter_allows(self):
        from alphaloop.pipeline.risk_gate import RiskGateRunner
        tool = _make_tool("risk_filter", passed=True)
        rg = RiskGateRunner(risk_filter_tool=tool)
        result = await rg.check(self._make_signal(), _make_context())
        assert result.allowed is True


# ---------------------------------------------------------------------------
# Stage 8: ExecutionGuard
# ---------------------------------------------------------------------------

class TestExecGuardTools:

    def _make_signal(self):
        from alphaloop.pipeline.types import CandidateSignal
        return CandidateSignal(
            direction="BUY",
            setup_type="pullback",
            entry_zone=(2749.0, 2751.0),
            stop_loss=2730.0,
            take_profit=[2780.0],
            raw_confidence=0.75,
            rr_ratio=1.6,
        )

    @pytest.mark.asyncio
    async def test_tick_jump_plugin_delay(self):
        from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
        tool = _make_tool("tick_jump_guard", passed=False, severity="warn")
        eg = ExecutionGuardRunner(tick_jump_tool=tool)
        result = await eg.check(self._make_signal(), _make_context())
        assert result.action == "DELAY"
        tool.timed_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_liq_vacuum_plugin_delay(self):
        from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
        tool = _make_tool("liq_vacuum_guard", passed=False, severity="warn")
        eg = ExecutionGuardRunner(liq_vacuum_tool=tool)
        result = await eg.check(self._make_signal(), _make_context())
        assert result.action == "DELAY"
        tool.timed_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_passing_plugins_execute(self):
        from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
        tj = _make_tool("tick_jump_guard", passed=True)
        lv = _make_tool("liq_vacuum_guard", passed=True)
        eg = ExecutionGuardRunner(tick_jump_tool=tj, liq_vacuum_tool=lv)
        result = await eg.check(self._make_signal(), _make_context())
        assert result.action == "EXECUTE"

    @pytest.mark.asyncio
    async def test_no_tools_falls_back_to_indicator(self):
        """With no plugins injected, indicator-read fallback still runs."""
        from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
        # tick_jump_atr high but no plugin → fallback reads from m15
        ctx = _make_context()
        ctx.indicators["M15"]["tick_jump_atr"] = 5.0  # > 0.8 threshold
        eg = ExecutionGuardRunner(tick_jump_atr_max=0.8)
        result = await eg.check(self._make_signal(), ctx)
        assert result.action == "DELAY"


# ---------------------------------------------------------------------------
# STAGE_TOOL_MAP integrity
# ---------------------------------------------------------------------------

class TestStageToolMap:

    def test_all_24_tools_assigned(self):
        from alphaloop.tools.registry import STAGE_TOOL_MAP, _DEFAULT_ORDER
        assigned = set()
        for tools in STAGE_TOOL_MAP.values():
            assigned.update(tools)
        all_known = set(_DEFAULT_ORDER.keys())
        unassigned = all_known - assigned
        assert unassigned == set(), f"Tools not assigned to any stage: {unassigned}"

    def test_no_tool_in_multiple_stages(self):
        from alphaloop.tools.registry import STAGE_TOOL_MAP
        seen = {}
        for stage, tools in STAGE_TOOL_MAP.items():
            for t in tools:
                assert t not in seen, (
                    f"Tool '{t}' assigned to both '{seen[t]}' and '{stage}'"
                )
                seen[t] = stage

    def test_quality_stage_has_6_tools(self):
        from alphaloop.tools.registry import STAGE_TOOL_MAP
        assert len(STAGE_TOOL_MAP["quality"]) == 6
