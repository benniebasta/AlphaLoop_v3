"""Unit tests for BoundedAIValidator mode parameter — soft veto in ai_signal mode."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from alphaloop.pipeline.ai_validator import BoundedAIValidator
from alphaloop.pipeline.types import (
    CandidateSignal,
    ConvictionScore,
    QualityResult,
    RegimeSnapshot,
)


def _make_caller(response: dict):
    caller = MagicMock()
    caller.call = AsyncMock(return_value=json.dumps(response))
    return caller


def _make_signal(raw_confidence=0.75):
    return CandidateSignal(
        direction="BUY",
        setup_type="pullback",
        entry_zone=(2749.0, 2751.0),
        stop_loss=2730.0,
        take_profit=[2780.0, 2800.0],
        raw_confidence=raw_confidence,
        rr_ratio=1.9,
        sl_source="swing_low",
        construction_candidates=2,
    )


def _make_regime():
    return RegimeSnapshot(regime="trending", macro_regime="neutral", volatility_band="normal")


def _make_quality():
    return QualityResult()


def _make_conviction():
    return ConvictionScore(score=72.0, decision="TRADE", size_scalar=1.0)


class TestAIValidatorModeAuthority:
    """AI reject in algo_ai → hard None; in ai_signal → soft confidence penalty."""

    @pytest.mark.asyncio
    async def test_algo_ai_reject_returns_none(self):
        """Default algo_ai mode: AI rejection is a hard veto."""
        validator = BoundedAIValidator(
            ai_caller=_make_caller({"status": "REJECTED", "reasoning": "risky"}),
            validator_model="claude-haiku-4-5",
        )
        result = await validator.validate(
            _make_signal(), _make_regime(), _make_quality(), _make_conviction(),
            context={}, mode="algo_ai",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_ai_signal_reject_returns_signal_with_penalty(self):
        """ai_signal mode: AI rejection → soft -0.15 confidence, not None."""
        validator = BoundedAIValidator(
            ai_caller=_make_caller({"status": "REJECTED", "reasoning": "advisory"}),
            validator_model="claude-haiku-4-5",
        )
        signal = _make_signal(raw_confidence=0.75)
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(),
            context={}, mode="ai_signal",
        )
        assert result is not None, "ai_signal reject should NOT return None"
        assert result.raw_confidence == pytest.approx(0.60, abs=0.01), (
            f"Expected 0.75 - 0.15 = 0.60, got {result.raw_confidence}"
        )
        # Direction/SL/TP must be unchanged
        assert result.direction == signal.direction
        assert result.stop_loss == signal.stop_loss

    @pytest.mark.asyncio
    async def test_ai_signal_soft_penalty_floor_at_030(self):
        """Soft confidence penalty must floor at 0.30."""
        validator = BoundedAIValidator(
            ai_caller=_make_caller({"status": "REJECTED", "reasoning": "advisory"}),
            validator_model="claude-haiku-4-5",
        )
        signal = _make_signal(raw_confidence=0.35)
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(),
            context={}, mode="ai_signal",
        )
        assert result is not None
        assert result.raw_confidence >= 0.30, (
            f"Soft penalty floor should be 0.30, got {result.raw_confidence}"
        )

    @pytest.mark.asyncio
    async def test_ai_signal_approve_passes_through(self):
        """ai_signal approve with no confidence change → original signal."""
        validator = BoundedAIValidator(
            ai_caller=_make_caller({"status": "APPROVED"}),
            validator_model="claude-haiku-4-5",
        )
        signal = _make_signal(raw_confidence=0.75)
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(),
            context={}, mode="ai_signal",
        )
        assert result is not None
        assert result.raw_confidence == signal.raw_confidence

    @pytest.mark.asyncio
    async def test_default_mode_is_algo_ai(self):
        """Default mode (no kwarg) is algo_ai — reject should return None."""
        validator = BoundedAIValidator(
            ai_caller=_make_caller({"status": "REJECTED", "reasoning": "risky"}),
            validator_model="claude-haiku-4-5",
        )
        result = await validator.validate(
            _make_signal(), _make_regime(), _make_quality(), _make_conviction(),
            context={},
            # No mode kwarg — defaults to "algo_ai"
        )
        assert result is None
