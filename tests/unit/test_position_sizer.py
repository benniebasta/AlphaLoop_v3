"""Tests for PositionSizer."""

import pytest

from alphaloop.core.types import ValidationStatus
from alphaloop.risk.sizer import PositionSizer
from alphaloop.signals.schema import TradeSignal, ValidatedSignal


def _make_validated(entry=2341.0, sl=2335.0, tp=None, risk_score=0.3):
    signal = TradeSignal(
        trend="bullish",
        setup="pullback",
        entry_zone=[2340.0, 2342.0],
        stop_loss=sl,
        take_profit=tp or [2348.0, 2355.0],
        confidence=0.85,
        reasoning="Strong pullback to EMA21 with structure intact",
    )
    return ValidatedSignal(
        original=signal,
        status=ValidationStatus.APPROVED,
        risk_score=risk_score,
    )


def test_basic_sizing():
    sizer = PositionSizer(10000.0, symbol="XAUUSD")
    validated = _make_validated()
    result = sizer.compute_lot_size(validated)
    assert result["lots"] > 0
    assert result["risk_amount_usd"] > 0
    assert result["sl_distance_points"] > 0


def test_high_risk_score_rejected():
    sizer = PositionSizer(10000.0, symbol="XAUUSD")
    validated = _make_validated(risk_score=0.90)
    with pytest.raises(ValueError, match="Risk score"):
        sizer.compute_lot_size(validated)


def test_wrong_side_sl_rejected():
    sizer = PositionSizer(10000.0, symbol="XAUUSD")
    # SELL with SL correctly above entry
    signal = TradeSignal(
        trend="bearish",
        setup="pullback",
        entry_zone=[2340.0, 2342.0],
        stop_loss=2360.0,  # SL above entry for SELL
        take_profit=[2310.0, 2300.0],
        confidence=0.85,
        reasoning="Bearish pullback setup at resistance level",
    )
    validated = ValidatedSignal(
        original=signal,
        status=ValidationStatus.APPROVED,
        risk_score=0.3,
    )
    result = sizer.compute_lot_size(validated)
    assert result["lots"] > 0


def test_macro_abort():
    sizer = PositionSizer(10000.0, symbol="XAUUSD")
    validated = _make_validated()
    with pytest.raises(ValueError, match="macro"):
        sizer.compute_lot_size(validated, macro_modifier=0.1)


def test_margin_cap():
    sizer = PositionSizer(1000.0, symbol="XAUUSD", margin_cap_pct=0.10)
    validated = _make_validated()
    result = sizer.compute_lot_size(validated)
    # With only 10% margin cap on $1000, lots should be small
    assert result["lots"] <= 0.5
