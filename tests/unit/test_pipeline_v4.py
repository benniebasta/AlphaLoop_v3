"""
Unit tests for the v4 institutional pipeline.

Covers: types, market_gate, regime, invalidation, freshness,
conviction (with penalty budget, conflict, quality floors),
execution_guard (delay mode), and ai_validator (bounded authority).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from alphaloop.pipeline.ai_validator import BoundedAIValidator
from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.freshness import compute_freshness
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.types import (
    CandidateSignal,
    ConvictionScore,
    InvalidationResult,
    PortfolioContext,
    QualityResult,
    RegimeSnapshot,
)


def _make_signal(**overrides):
    defaults = dict(
        direction="BUY",
        setup_type="pullback",
        entry_zone=(3100.0, 3102.0),
        stop_loss=3090.0,
        take_profit=[3115.0],
        raw_confidence=0.75,
        rr_ratio=1.5,
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _make_regime(**overrides):
    defaults = dict(
        regime="trending",
        macro_regime="neutral",
        volatility_band="normal",
        allowed_setups=["pullback", "breakout", "continuation"],
        confidence_ceiling=95.0,
        min_entry_adjustment=-5.0,
        size_multiplier=1.1,
    )
    defaults.update(overrides)
    return RegimeSnapshot(**defaults)


def _make_quality(**overrides):
    defaults = dict(
        tool_scores={"ema200": 80, "bos": 70, "macd": 60},
        group_scores={
            "trend": 75,
            "momentum": 65,
            "structure": 70,
            "volume": 55,
            "volatility": 60,
        },
        overall_score=67.0,
        contradictions=[],
        low_score_count=0,
        max_score=80.0,
    )
    defaults.update(overrides)
    return QualityResult(**defaults)


def _make_context(**overrides):
    ctx = MagicMock()
    ctx.timeframe = "M15"
    ctx.indicators = {
        "M15": {
            "ema200": 3090.0,
            "atr": 10.0,
            "bos": {
                "bullish_bos": True,
                "bullish_break_atr": 0.5,
                "bearish_bos": False,
                "bearish_break_atr": 0,
            },
            "swing_structure": "bullish",
            "fvg": {
                "bullish": [{"size_atr": 0.3, "bottom": 3095, "top": 3098}],
                "bearish": [],
            },
            "vwap": 3095.0,
            "bb_pct_b": 0.4,
            "fast_fingers": {
                "is_exhausted_up": False,
                "is_exhausted_down": True,
                "exhaustion_score": 60,
            },
            "choppiness": 35.0,
            "adx": 30.0,
            "tick_jump_atr": 0.3,
            "liq_vacuum": {"bar_range_atr": 1.0, "body_pct": 60},
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
    ctx.open_trades = []
    ctx.risk_monitor = SimpleNamespace(
        kill_switch_active=False,
        _kill_switch_active=False,
    )
    ctx.df = MagicMock(__len__=lambda self: 500)
    ctx.pip_size = 0.01
    ctx.trade_direction = ""
    ctx.symbol = "XAUUSD"

    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestMarketGate:
    def test_kill_switch_blocks(self):
        gate = MarketGate()
        ctx = _make_context()
        ctx.risk_monitor.kill_switch_active = True
        ctx.risk_monitor._kill_switch_active = True
        result = asyncio.run(gate.check(ctx))
        assert not result.tradeable
        assert result.blocked_by == "kill_switch"

    def test_normal_passes(self):
        gate = MarketGate()
        ctx = _make_context()
        result = asyncio.run(gate.check(ctx))
        assert result.tradeable

    def test_stale_feed_blocks(self):
        gate = MarketGate()
        ctx = _make_context()
        ctx.price.time = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = asyncio.run(gate.check(ctx))
        assert not result.tradeable
        assert result.blocked_by == "stale_feed"

    def test_feed_desync_blocks(self):
        gate = MarketGate()
        ctx = _make_context()
        ctx.price.bid = 3102.0
        ctx.price.ask = 3100.0
        result = asyncio.run(gate.check(ctx))
        assert not result.tradeable
        assert result.blocked_by == "feed_desync"


class TestRegimeClassifier:
    def test_trending(self):
        cls = RegimeClassifier()
        ctx = _make_context()
        ctx.indicators["M15"]["choppiness"] = 30.0
        ctx.indicators["M15"]["adx"] = 35.0
        result = asyncio.run(cls.classify(ctx))
        assert result.regime == "trending"
        assert "pullback" in result.allowed_setups
        assert result.size_multiplier == 1.1

    def test_ranging(self):
        cls = RegimeClassifier()
        ctx = _make_context()
        ctx.indicators["M15"]["choppiness"] = 70.0
        ctx.indicators["M15"]["adx"] = 12.0
        result = asyncio.run(cls.classify(ctx))
        assert result.regime == "ranging"
        assert "breakout" not in result.allowed_setups
        assert result.size_multiplier == 0.8

    def test_volatile(self):
        cls = RegimeClassifier()
        ctx = _make_context()
        ctx.indicators["M15"]["choppiness"] = 50.0
        ctx.indicators["M15"]["adx"] = 20.0
        ctx.indicators["H1"]["atr_pct"] = 0.008
        result = asyncio.run(cls.classify(ctx))
        assert result.regime == "volatile"
        assert result.size_multiplier == 0.6


class TestInvalidation:
    def _inv(self, **cfg_overrides):
        cfg = {"sl_min_points": 100, "sl_max_points": 3000}
        cfg.update(cfg_overrides)
        return StructuralInvalidator(cfg=cfg)

    def test_valid_signal_passes(self):
        inv = self._inv()
        sig = _make_signal()
        regime = _make_regime()
        ctx = _make_context()
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "PASS"

    def test_sl_wrong_side_hard(self):
        inv = self._inv()
        sig = _make_signal(stop_loss=3110.0)
        regime = _make_regime()
        ctx = _make_context()
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "HARD_INVALIDATE"

    def test_rr_below_1_hard(self):
        inv = self._inv()
        sig = _make_signal(rr_ratio=0.8)
        regime = _make_regime()
        ctx = _make_context()
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "HARD_INVALIDATE"

    def test_rr_borderline_soft(self):
        inv = self._inv()
        sig = _make_signal(rr_ratio=1.3)
        regime = _make_regime()
        ctx = _make_context()
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "SOFT_INVALIDATE"
        assert result.conviction_penalty > 0

    def test_breakout_no_bos_hard(self):
        inv = self._inv()
        sig = _make_signal(setup_type="breakout")
        regime = _make_regime()
        ctx = _make_context()
        ctx.indicators["M15"]["bos"]["bullish_bos"] = False
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "HARD_INVALIDATE"

    def test_breakout_weak_bos_soft(self):
        inv = self._inv()
        sig = _make_signal(setup_type="breakout")
        regime = _make_regime()
        ctx = _make_context()
        ctx.indicators["M15"]["bos"]["bullish_break_atr"] = 0.1
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "SOFT_INVALIDATE"

    def test_wrong_regime_setup_hard(self):
        inv = self._inv()
        sig = _make_signal(setup_type="range_bounce")
        regime = _make_regime()
        ctx = _make_context()
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "HARD_INVALIDATE"

    def test_reversal_no_exhaustion_hard(self):
        inv = self._inv()
        sig = _make_signal(setup_type="reversal", direction="BUY")
        regime = _make_regime(allowed_setups=["reversal", "pullback"])
        ctx = _make_context()
        ctx.indicators["M15"]["fast_fingers"]["is_exhausted_down"] = False
        result = asyncio.run(inv.validate(sig, regime, ctx))
        assert result.severity == "HARD_INVALIDATE"


class TestFreshness:
    def test_fresh_signal(self):
        sig = _make_signal()
        f = compute_freshness(sig, current_price=3101.0, atr=10.0)
        assert f == 1.0

    def test_moderate_decay(self):
        sig = _make_signal()
        f = compute_freshness(sig, current_price=3106.0, atr=10.0)
        assert 0 < f < 1.0

    def test_reject_too_far(self):
        sig = _make_signal()
        f = compute_freshness(sig, current_price=3112.0, atr=10.0)
        assert f == 0.0

    def test_time_decay(self):
        sig = _make_signal()
        f = compute_freshness(sig, current_price=3101.0, atr=10.0, candles_elapsed=4)
        assert f < 1.0

    def test_reject_too_old(self):
        sig = _make_signal()
        f = compute_freshness(sig, current_price=3101.0, atr=10.0, candles_elapsed=6)
        assert f == 0.0


class TestConviction:
    def test_normal_trade(self):
        scorer = ConvictionScorer()
        q = _make_quality()
        r = _make_regime()
        inv = InvalidationResult(severity="PASS")
        result = scorer.score(q, r, inv)
        assert result.decision == "TRADE"
        assert result.score > 0

    def test_quality_floor_overall(self):
        scorer = ConvictionScorer()
        q = _make_quality(overall_score=30.0)
        r = _make_regime()
        result = scorer.score(q, r, InvalidationResult(severity="PASS"))
        assert result.decision == "HOLD"
        assert result.quality_floor_triggered

    def test_quality_floor_contradictions(self):
        scorer = ConvictionScorer()
        q = _make_quality(low_score_count=4)
        r = _make_regime()
        result = scorer.score(q, r, InvalidationResult(severity="PASS"))
        assert result.decision == "HOLD"
        assert result.quality_floor_triggered

    def test_quality_floor_max_score(self):
        scorer = ConvictionScorer()
        q = _make_quality(max_score=50.0)
        r = _make_regime()
        result = scorer.score(q, r, InvalidationResult(severity="PASS"))
        assert result.decision == "HOLD"

    def test_conflict_penalty(self):
        scorer = ConvictionScorer()
        q_conflict = _make_quality(
            group_scores={"trend": 90, "momentum": 15, "structure": 70, "volume": 60, "volatility": 50}
        )
        q_no_conflict = _make_quality()
        r = _make_regime()
        inv = InvalidationResult(severity="PASS")
        c1 = scorer.score(q_conflict, r, inv)
        c2 = scorer.score(q_no_conflict, r, inv)
        assert c1.conflict_penalty > 0
        assert c1.score < c2.score

    def test_penalty_budget_cap(self):
        scorer = ConvictionScorer()
        q = _make_quality(
            group_scores={"trend": 90, "momentum": 15, "structure": 70, "volume": 60, "volatility": 50}
        )
        r = _make_regime(
            portfolio_context=PortfolioContext(
                macro_exposure="USD_long",
                risk_budget_remaining_pct=0.01,
            )
        )
        inv = InvalidationResult(severity="SOFT_INVALIDATE", conviction_penalty=40.0)
        result = scorer.score(q, r, inv)
        assert result.total_penalty <= 50.0
        assert result.penalties_prorated

    def test_regime_ceiling(self):
        scorer = ConvictionScorer()
        q = _make_quality(
            group_scores={"trend": 95, "momentum": 95, "structure": 95, "volume": 95, "volatility": 95},
            overall_score=95.0,
            max_score=95.0,
        )
        r = _make_regime(
            regime="ranging",
            confidence_ceiling=80.0,
            allowed_setups=["range_bounce", "reversal", "pullback"],
        )
        result = scorer.score(q, r, InvalidationResult(severity="PASS"))
        assert result.score <= 80.0


class TestExecutionGuard:
    def test_clean_execution(self):
        guard = ExecutionGuardRunner()
        sig = _make_signal()
        ctx = _make_context()
        result = asyncio.run(guard.check(sig, ctx, symbol="XAUUSD"))
        assert result.action == "EXECUTE"

    def test_tick_jump_delays(self):
        guard = ExecutionGuardRunner()
        sig = _make_signal()
        ctx = _make_context()
        ctx.indicators["M15"]["tick_jump_atr"] = 1.2
        result = asyncio.run(guard.check(sig, ctx, symbol="XAUUSD"))
        assert result.action == "DELAY"
        assert result.delay_candles >= 1

    def test_delay_queue(self):
        guard = ExecutionGuardRunner()
        sig = _make_signal()
        guard.queue_delay("XAUUSD", sig, "test delay")
        ds = guard.get_delayed("XAUUSD")
        assert ds is not None
        assert ds.signal.direction == "BUY"
        ds = guard.tick_delay("XAUUSD")
        assert ds is not None
        guard.clear_delay("XAUUSD")
        assert guard.get_delayed("XAUUSD") is None


class TestAIValidator:
    def test_auto_approve_no_caller(self):
        validator = BoundedAIValidator()
        sig = _make_signal()
        result = asyncio.run(
            validator.validate(
                sig,
                _make_regime(),
                _make_quality(),
                ConvictionScore(),
                _make_context(),
            )
        )
        assert result is not None
        assert result.direction == "BUY"

    def test_rejects_sl_wrong_side(self):
        validator = BoundedAIValidator(
            ai_caller=MagicMock(),
            validator_model="test",
            sl_min_points=100,
            sl_max_points=3000,
        )
        parsed = {"status": "approved", "adjusted_sl": 3105.0}
        sig = _make_signal()
        result = validator._apply_adjustments(sig, parsed, _make_context())
        assert result is None or result.stop_loss < sig.entry_zone[0]

    def test_rejects_rr_below_min(self):
        validator = BoundedAIValidator(min_rr=1.5, sl_min_points=100, sl_max_points=3000)
        parsed = {"status": "approved", "adjusted_tp": [3103.0]}
        sig = _make_signal()
        result = validator._apply_adjustments(sig, parsed, _make_context())
        assert result is None or result.take_profit[0] > 3103.0

    def test_confidence_cap(self):
        validator = BoundedAIValidator(sl_min_points=100, sl_max_points=3000, min_rr=1.0)
        parsed = {"status": "approved", "confidence": 0.99}
        sig = _make_signal(raw_confidence=0.70)
        result = validator._apply_adjustments(sig, parsed, _make_context())
        assert result is not None
        assert result.raw_confidence <= 0.75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
