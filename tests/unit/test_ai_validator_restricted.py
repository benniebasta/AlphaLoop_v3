"""Unit tests for pipeline/ai_validator.py — restricted to confidence-only adjustments."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from alphaloop.pipeline.ai_validator import BoundedAIValidator
from alphaloop.pipeline.types import (
    CandidateSignal,
    ConvictionScore,
    QualityResult,
    RegimeSnapshot,
)


def _make_caller(response_json: str):
    """Create a mock AI caller with a .call() async method."""
    caller = MagicMock()
    caller.call = AsyncMock(return_value=response_json)
    return caller


def _make_signal(**overrides):
    defaults = dict(
        direction="BUY",
        setup_type="pullback",
        entry_zone=(2749.0, 2751.0),
        stop_loss=2730.0,
        take_profit=[2780.0, 2800.0],
        raw_confidence=0.75,
        rr_ratio=1.9,
        sl_source="swing_low",
        construction_candidates=2,
    )
    defaults.update(overrides)
    return CandidateSignal(**defaults)


def _make_regime():
    return RegimeSnapshot(
        regime="trending",
        macro_regime="neutral",
        volatility_band="normal",
    )


def _make_quality():
    return QualityResult()


def _make_conviction():
    return ConvictionScore(
        score=72.0,
        decision="TRADE",
        size_scalar=1.0,
    )


class TestAICannotMutatePriceLevels:
    """AI validator must NOT modify entry, SL, or TP."""

    @pytest.mark.asyncio
    async def test_ai_cannot_mutate_sl(self):
        """AI response with adjusted_sl should be ignored."""
        caller = _make_caller('{"status": "approved", "adjusted_sl": 2725.0}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal()
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is not None
        assert result.stop_loss == signal.stop_loss  # Unchanged

    @pytest.mark.asyncio
    async def test_ai_cannot_mutate_tp(self):
        """AI response with adjusted_tp should be ignored."""
        caller = _make_caller('{"status": "approved", "adjusted_tp": [2790.0]}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal()
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is not None
        assert result.take_profit == signal.take_profit  # Unchanged

    @pytest.mark.asyncio
    async def test_ai_cannot_mutate_entry(self):
        """AI response with adjusted_entry should be ignored."""
        caller = _make_caller('{"status": "approved", "adjusted_entry": 2752.0}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal()
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is not None
        assert result.entry_zone == signal.entry_zone  # Unchanged


class TestAICanAdjustConfidence:
    @pytest.mark.asyncio
    async def test_confidence_reduced(self):
        """AI may reduce confidence."""
        caller = _make_caller('{"status": "approved", "confidence": 0.60}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal(raw_confidence=0.75)
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is not None
        assert result.raw_confidence == 0.60

    @pytest.mark.asyncio
    async def test_confidence_boost_capped(self):
        """AI can boost confidence by at most 0.05."""
        caller = _make_caller('{"status": "approved", "confidence": 0.95}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal(raw_confidence=0.75)
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is not None
        assert result.raw_confidence <= 0.80  # 0.75 + 0.05


class TestAICanReject:
    @pytest.mark.asyncio
    async def test_rejection_returns_none(self):
        """AI rejecting the signal should return None."""
        caller = _make_caller('{"status": "rejected", "reasoning": "bad setup"}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal()
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is None


class TestPreservesConstructionProvenance:
    @pytest.mark.asyncio
    async def test_sl_source_preserved(self):
        """Construction provenance fields must survive AI validation."""
        caller = _make_caller('{"status": "approved", "confidence": 0.70}')
        validator = BoundedAIValidator(ai_caller=caller, validator_model="test")

        signal = _make_signal(sl_source="swing_low", construction_candidates=3)
        result = await validator.validate(
            signal, _make_regime(), _make_quality(), _make_conviction(), {},
        )

        assert result is not None
        assert result.sl_source == "swing_low"
        assert result.construction_candidates == 3
