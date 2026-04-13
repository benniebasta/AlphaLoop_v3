"""
End-to-end integration test for the v4 pipeline orchestrator.

Simulates a full cycle through all 8 stages with realistic mock data.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.orchestrator import PipelineOrchestrator
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.risk_gate import RiskGateRunner
from alphaloop.pipeline.types import CandidateSignal, CycleOutcome


def _make_context():
    """Build a realistic mock MarketContext."""
    ctx = MagicMock()
    ctx.symbol = "XAUUSD"
    ctx.trade_direction = ""
    ctx.pip_size = 0.01

    ctx.indicators = {
        "M15": {
            "ema200": 3090.0,
            "atr": 10.0,
            "choppiness": 32.0,
            "adx": 35.0,
            "bos": {
                "bullish_bos": True,
                "bullish_break_atr": 0.5,
                "bearish_bos": False,
                "bearish_break_atr": 0,
                "swing_high": 3098.0,
                "swing_low": 3080.0,
            },
            "fvg": {
                "bullish": [{"size_atr": 0.3, "bottom": 3092, "top": 3095, "midpoint": 3093.5}],
                "bearish": [],
            },
            "vwap": 3095.0,
            "swing_structure": "bullish",
            "bb_pct_b": 0.45,
            "fast_fingers": {"is_exhausted_up": False, "is_exhausted_down": True, "exhaustion_score": 60},
            "tick_jump_atr": 0.3,
            "liq_vacuum": {"bar_range_atr": 1.0, "body_pct": 60},
            "median_spread": 1.5,
        },
        "H1": {"atr_pct": 0.003},
    }

    ctx.session = MagicMock(is_weekend=False, score=0.85)
    ctx.price = MagicMock(
        bid=3100.5,
        ask=3101.0,
        spread=1.5,
        time=datetime.now(timezone.utc),
    )
    ctx.news = []
    ctx.dxy = None
    ctx.sentiment = None
    ctx.open_trades = {}
    ctx.risk_monitor = SimpleNamespace(
        kill_switch_active=False,
        _kill_switch_active=False,
        _open_risk_usd=0,
        account_balance=10000,
        can_open_trade=AsyncMock(return_value=(True, "")),
    )
    ctx.df = MagicMock(__len__=lambda self: 500)
    ctx.timeframe = "M15"
    return ctx


def _make_signal(direction="BUY", setup="pullback", confidence=0.75):
    return CandidateSignal(
        direction=direction,
        setup_type=setup,
        entry_zone=(3100.0, 3102.0),
        stop_loss=3090.0,
        take_profit=[3115.0],
        raw_confidence=confidence,
        rr_ratio=1.5,
        signal_sources=["ema_crossover"],
        reasoning="Test signal",
    )


class TestOrchestratorE2E:
    def test_successful_trade_algo_only(self):
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal()

        scorer = ConvictionScorer()
        scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
            conviction_scorer=scorer,
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.TRADE_OPENED
        assert result.market_gate is not None
        assert result.market_gate.tradeable
        assert result.regime is not None
        assert result.regime.regime == "trending"
        assert result.signal is not None
        assert result.conviction is not None
        assert result.conviction.decision == "TRADE"
        assert result.sizing is not None
        assert result.sizing.regime_scalar == 1.1
        assert result.elapsed_ms > 0
        assert result.journey.final_outcome == CycleOutcome.TRADE_OPENED.value
        assert [stage.stage for stage in result.journey.stages][:4] == [
            "market_gate", "regime", "signal", "invalidation"
        ]
        assert result.journey.stages[-1].stage == "sizing"

    def test_blocking_market_gate_plugin_rejects(self):
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal()

        plugin = MagicMock()
        plugin.name = "session_filter"
        plugin.timed_run = AsyncMock(
            return_value=SimpleNamespace(
                tool_name="session_filter",
                passed=False,
                reason="Weekend - market closed",
                severity="block",
                size_modifier=1.0,
            )
        )

        orchestrator = PipelineOrchestrator(market_gate=MarketGate(tools=[plugin]))
        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))

        assert result.outcome == CycleOutcome.REJECTED
        assert result.market_gate is not None
        assert result.market_gate.blocked_by == "session_filter"
        assert result.market_gate.tradeable is False
        assert result.signal is None
        assert result.journey.final_outcome == CycleOutcome.REJECTED.value
        assert len(result.journey.stages) == 1
        assert result.journey.stages[0].stage == "market_gate"
        assert result.journey.stages[0].status == "blocked"

    def test_no_signal_produces_no_signal(self):
        ctx = _make_context()

        async def signal_gen(context, regime):
            return None

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        )
        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.NO_SIGNAL

    def test_wrong_regime_setup_holds(self):
        ctx = _make_context()
        ctx.indicators["M15"]["choppiness"] = 70.0
        ctx.indicators["M15"]["adx"] = 12.0

        async def signal_gen(context, regime):
            return _make_signal(setup="breakout")

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.HELD
        assert "breakout" in (result.rejection_reason or "").lower()

    def test_hard_invalidation_rejects(self):
        ctx = _make_context()

        async def signal_gen(context, regime):
            sig = _make_signal()
            sig.stop_loss = 3110.0
            return sig

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.REJECTED
        assert result.invalidation is not None
        assert result.invalidation.severity == "HARD_INVALIDATE"

    def test_tick_jump_delays(self):
        ctx = _make_context()
        ctx.indicators["M15"]["tick_jump_atr"] = 1.5

        async def signal_gen(context, regime):
            return _make_signal()

        scorer = ConvictionScorer()
        scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
            conviction_scorer=scorer,
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.DELAYED
        assert result.execution_guard is not None
        assert result.execution_guard.action == "DELAY"

    def test_low_conviction_holds(self):
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal(confidence=0.35)

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
            conviction_scorer=ConvictionScorer(),
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.HELD
        assert result.conviction is not None
        assert result.conviction.decision == "HOLD"

    def test_full_waterfall_populated(self):
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal()

        scorer = ConvictionScorer()
        scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
            conviction_scorer=scorer,
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.market_gate is not None
        assert result.regime is not None
        assert result.signal is not None
        assert result.invalidation is not None
        assert result.quality is not None
        assert result.conviction is not None
        assert result.risk_gate is not None
        assert result.execution_guard is not None
        assert result.sizing is not None
        c = result.conviction
        assert c.regime_min_entry > 0
        assert c.regime_ceiling > 0
        assert c.penalty_budget_cap == 50.0
        assert len(c.reasoning) > 0

    def test_delay_queue_lifecycle(self):
        guard = ExecutionGuardRunner(max_delay_candles=3)
        sig = _make_signal()
        guard.queue_delay("XAUUSD", sig, "test spread spike")
        assert guard.get_delayed("XAUUSD") is not None

        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        assert ds.candles_waited == 1

        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        assert ds.candles_waited == 2

        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        assert ds.candles_waited == 3

        ds = guard.tick_delay("XAUUSD")
        assert ds is None
        assert guard.get_delayed("XAUUSD") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
