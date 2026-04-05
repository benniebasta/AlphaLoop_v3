"""
End-to-end integration test for the v4 pipeline orchestrator.

Simulates a full cycle through all 8 stages with realistic mock data.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone

from alphaloop.pipeline.types import (
    CandidateSignal,
    CycleOutcome,
    RegimeSnapshot,
)
from alphaloop.pipeline.orchestrator import PipelineOrchestrator, PipelineResult
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.quality import StructuralQuality
from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.risk_gate import RiskGateRunner


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
        "H1": {
            "atr_pct": 0.003,
        },
    }

    ctx.session = MagicMock(is_weekend=False, score=0.85)
    ctx.price = MagicMock(
        bid=3100.5, ask=3101.0, spread=1.5,
        time=datetime.now(timezone.utc),
    )
    ctx.news = []
    ctx.dxy = None
    ctx.sentiment = None
    ctx.open_trades = {}
    ctx.risk_monitor = MagicMock(_kill_switch_active=False, _open_risk_usd=0, account_balance=10000)
    ctx.risk_monitor.can_open_trade = AsyncMock(return_value=(True, ""))
    ctx.df = MagicMock(__len__=lambda self: 500)

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
    """Full pipeline end-to-end tests."""

    def test_successful_trade_algo_only(self):
        """A clean signal should pass all 8 stages and produce TRADE_OPENED."""
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal()

        # Use lower min_entry so neutral scores (50) can pass in trending regime
        # In production, quality tools would provide real scores >60
        scorer = ConvictionScorer()
        scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={
                "sl_min_points": 100, "sl_max_points": 3000,
            }),
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
        assert result.sizing.regime_scalar == 1.1  # trending
        assert result.elapsed_ms > 0

    def test_blocking_market_gate_plugin_rejects(self):
        """A blocking session_filter plugin should produce REJECTED at Stage 1."""
        from unittest.mock import AsyncMock, MagicMock
        from types import SimpleNamespace

        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal()

        # Simulate session_filter blocking (e.g. weekend / outside session)
        plugin = MagicMock()
        plugin.name = "session_filter"
        plugin.timed_run = AsyncMock(return_value=SimpleNamespace(
            tool_name="session_filter",
            passed=False,
            reason="Weekend — market closed",
            severity="block",
            size_modifier=1.0,
        ))

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(tools=[plugin]),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))

        assert result.outcome == CycleOutcome.REJECTED
        assert result.market_gate is not None
        assert result.market_gate.blocked_by == "session_filter"
        assert result.market_gate.tradeable is False
        # Should not have reached signal generation
        assert result.signal is None

    def test_no_signal_produces_no_signal(self):
        """When signal generator returns None, outcome is NO_SIGNAL."""
        ctx = _make_context()

        async def signal_gen(context, regime):
            return None

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.NO_SIGNAL

    def test_wrong_regime_setup_holds(self):
        """Setup type not in regime's allowed list → HELD."""
        ctx = _make_context()
        ctx.indicators["M15"]["choppiness"] = 70.0  # ranging
        ctx.indicators["M15"]["adx"] = 12.0

        async def signal_gen(context, regime):
            # breakout not allowed in ranging regime
            return _make_signal(setup="breakout")

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={
                "sl_min_points": 100, "sl_max_points": 3000,
            }),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.HELD
        assert "breakout" in (result.rejection_reason or "").lower()

    def test_hard_invalidation_rejects(self):
        """SL on wrong side → HARD_INVALIDATE → REJECTED."""
        ctx = _make_context()

        async def signal_gen(context, regime):
            sig = _make_signal()
            sig.stop_loss = 3110.0  # SL above entry for BUY
            return sig

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={
                "sl_min_points": 100, "sl_max_points": 3000,
            }),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.REJECTED
        assert result.invalidation is not None
        assert result.invalidation.severity == "HARD_INVALIDATE"

    def test_tick_jump_delays(self):
        """Tick jump at execution time → DELAYED."""
        ctx = _make_context()
        ctx.indicators["M15"]["tick_jump_atr"] = 1.5  # > 0.8 threshold

        async def signal_gen(context, regime):
            return _make_signal()

        scorer = ConvictionScorer()
        scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={
                "sl_min_points": 100, "sl_max_points": 3000,
            }),
            conviction_scorer=scorer,
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))
        assert result.outcome == CycleOutcome.DELAYED
        assert result.execution_guard is not None
        assert result.execution_guard.action == "DELAY"

    def test_low_conviction_holds(self):
        """Very low quality scores → HELD by conviction scorer."""
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal(confidence=0.35)

        # ConvictionScorer without quality tools will use neutral (50) for all groups
        # Confidence 0.35 is above the hard floor, but without quality tools the score
        # depends on default neutral scores
        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={
                "sl_min_points": 100, "sl_max_points": 3000,
            }),
            conviction_scorer=ConvictionScorer(),
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))

        # With no quality tools, group scores default to 50, conviction ≈ 50
        # Trending regime min_entry = 60 - 5 = 55. Score 50 < 55 → HOLD
        assert result.outcome == CycleOutcome.HELD
        assert result.conviction is not None
        assert result.conviction.decision == "HOLD"

    def test_full_waterfall_populated(self):
        """Verify all pipeline result fields are populated on successful trade."""
        ctx = _make_context()

        async def signal_gen(context, regime):
            return _make_signal()

        scorer = ConvictionScorer()
        scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}

        orchestrator = PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={
                "sl_min_points": 100, "sl_max_points": 3000,
            }),
            conviction_scorer=scorer,
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

        result = asyncio.run(orchestrator.run(ctx, signal_gen, symbol="XAUUSD"))

        # Every stage should have produced output
        assert result.market_gate is not None
        assert result.regime is not None
        assert result.signal is not None
        assert result.invalidation is not None
        assert result.quality is not None
        assert result.conviction is not None
        assert result.risk_gate is not None
        assert result.execution_guard is not None
        assert result.sizing is not None

        # Conviction waterfall should have accounting
        c = result.conviction
        assert c.regime_min_entry > 0
        assert c.regime_ceiling > 0
        assert c.penalty_budget_cap == 50.0
        assert len(c.reasoning) > 0

    def test_delay_queue_lifecycle(self):
        """Test delay → re-check → execute flow."""
        guard = ExecutionGuardRunner(max_delay_candles=3)
        sig = _make_signal()

        # Queue a delay
        guard.queue_delay("XAUUSD", sig, "test spread spike")
        assert guard.get_delayed("XAUUSD") is not None

        # Tick 1: still delayed
        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        assert ds.candles_waited == 1

        # Tick 2
        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        assert ds.candles_waited == 2

        # Tick 3
        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        assert ds.candles_waited == 3

        # Tick 4: expired (max was 3)
        ds = guard.tick_delay("XAUUSD")
        assert ds is None
        assert guard.get_delayed("XAUUSD") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
