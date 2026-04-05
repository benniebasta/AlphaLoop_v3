"""Unit tests for pipeline/construction.py — constraint-first trade construction."""

import pytest
from datetime import datetime, timezone

from alphaloop.pipeline.construction import TradeConstructor, ConstructionResult
from alphaloop.pipeline.types import DirectionHypothesis


def _make_hypothesis(direction="BUY", confidence=0.75, setup="pullback"):
    return DirectionHypothesis(
        direction=direction,
        confidence=confidence,
        setup_tag=setup,
        reasoning="test hypothesis",
        source_names="ema_crossover",
        generated_at=datetime.now(timezone.utc),
    )


def _make_constructor(**overrides):
    defaults = dict(
        pip_size=0.1,          # XAUUSD
        sl_min_pts=150.0,
        sl_max_pts=500.0,
        tp1_rr=1.5,
        tp2_rr=2.5,
        entry_zone_atr_mult=0.25,
        sl_buffer_atr=0.15,
    )
    defaults.update(overrides)
    return TradeConstructor(**defaults)


def _make_indicators(
    *,
    swing_lows=None,
    swing_highs=None,
    fvg_bullish=None,
    fvg_bearish=None,
):
    return {
        "swing_lows": swing_lows or [],
        "swing_highs": swing_highs or [],
        "fvg": {
            "bullish": fvg_bullish or [],
            "bearish": fvg_bearish or [],
            "has_bullish": bool(fvg_bullish),
            "has_bearish": bool(fvg_bearish),
        },
    }


# ── No structure → no trade ───────────────────────────────────────────────────


class TestNoStructure:
    def test_no_swing_no_fvg_uses_atr_fallback(self):
        """With no structure, ATR fallback should produce a trade."""
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators()
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "atr_fallback"

    def test_no_structure_no_fallback_when_disabled(self):
        """With sl_atr_mult=0, no ATR fallback — rejects like before."""
        tc = _make_constructor(sl_atr_mult=0)
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators()
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is None
        assert "no valid SL" in result.rejection_reason


# ── ATR fallback ─────────────────────────────────────────────────────────────


class TestATRFallback:
    def test_atr_fallback_buy_lowest_priority(self):
        """When valid swing exists, ATR fallback is not used."""
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2730.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "swing_low"  # structure preferred over fallback

    def test_atr_fallback_sell(self):
        """SELL side: ATR fallback places SL above entry."""
        tc = _make_constructor()
        hyp = _make_hypothesis("SELL")
        indicators = _make_indicators()
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "atr_fallback"
        assert result.signal.stop_loss > 2750.0

    def test_atr_fallback_respects_bounds(self):
        """ATR fallback must also satisfy sl_min/sl_max bounds."""
        # sl_atr_mult=0.5, ATR=10 → SL distance ~5 price → 50 pts (< min 150)
        tc = _make_constructor(sl_atr_mult=0.5)
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators()
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is None  # 50 pts < 150 min


# ── SL bounds checking ───────────────────────────────────────────────────────


class TestSLBounds:
    def test_sl_too_small_rejects_swing_uses_fallback(self):
        """Swing low 5 pts away (< min 150) rejected; ATR fallback used instead."""
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        # Swing low only 0.5 price away → 5 pts for pip_size=0.1
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2750.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "atr_fallback"  # swing rejected, fallback used

    def test_sl_too_small_no_trade_without_fallback(self):
        """Swing low 5 pts away, no ATR fallback → no trade."""
        tc = _make_constructor(sl_atr_mult=0)
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2750.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is None

    def test_sl_too_large_rejects_swing_uses_fallback(self):
        """Swing low 600 pts away (> max 500) rejected; ATR fallback used instead."""
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        # Swing low 60 price away → 600 pts for pip_size=0.1
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2690.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "atr_fallback"  # swing rejected, fallback used

    def test_sl_too_large_no_trade_without_fallback(self):
        """Swing low 600 pts away, no ATR fallback → no trade."""
        tc = _make_constructor(sl_atr_mult=0)
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2690.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is None


# ── Valid construction ────────────────────────────────────────────────────────


class TestValidConstruction:
    def test_valid_swing_sl_emits_trade(self):
        """Swing low producing ~200 pts SL → valid trade."""
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        # Swing low 20 price away → 200 pts (+ buffer moves it a bit further)
        # ATR=10, buffer=0.15*10=1.5 → SL at 2728.5, distance ~22 → 220 pts
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2730.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "swing_low"
        assert result.signal.direction == "BUY"
        assert result.signal.stop_loss < 2750.0
        assert len(result.signal.take_profit) == 2
        assert result.signal.rr_ratio >= 1.5

    def test_fvg_fallback_when_swing_fails(self):
        """When swing low is too close, FVG bottom should be tried."""
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        # Swing low too close (5 pts), but FVG bottom at good distance
        indicators = _make_indicators(
            swing_lows=[{"index": 10, "price": 2750.0}],  # too close
            fvg_bullish=[{"top": 2732.0, "bottom": 2728.0, "midpoint": 2730.0, "size_atr": 0.4}],
        )
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "fvg_bottom"

    def test_tp_derived_from_sl(self):
        """TP must be entry + sl_distance * rr_target."""
        tc = _make_constructor(tp1_rr=2.0, tp2_rr=3.0)
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2730.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None

        entry = 2750.5  # ask for BUY
        sl_dist = abs(entry - result.signal.stop_loss)
        tp1_expected = entry + sl_dist * 2.0
        tp2_expected = entry + sl_dist * 3.0
        assert result.signal.take_profit[0] == pytest.approx(tp1_expected, abs=0.01)
        assert result.signal.take_profit[1] == pytest.approx(tp2_expected, abs=0.01)

    def test_rr_always_valid_by_construction(self):
        """R:R must be >= tp1_rr by construction."""
        tc = _make_constructor(tp1_rr=1.5)
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2730.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.signal.rr_ratio >= 1.5


# ── SELL side ─────────────────────────────────────────────────────────────────


class TestSellSide:
    def test_sell_uses_swing_high(self):
        tc = _make_constructor()
        hyp = _make_hypothesis("SELL")
        # Swing high ~20 price above entry → 200 pts
        indicators = _make_indicators(swing_highs=[{"index": 10, "price": 2770.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "swing_high"
        assert result.signal.stop_loss > 2750.0

    def test_sell_uses_bearish_fvg(self):
        tc = _make_constructor()
        hyp = _make_hypothesis("SELL")
        indicators = _make_indicators(
            fvg_bearish=[{"top": 2772.0, "bottom": 2768.0, "midpoint": 2770.0, "size_atr": 0.4}],
        )
        result = tc.construct(hyp, bid=2750.0, ask=2750.5, indicators=indicators, atr=10.0)
        assert result.signal is not None
        assert result.sl_source == "fvg_top"


# ── Entry price ───────────────────────────────────────────────────────────────


class TestEntryPrice:
    def test_buy_uses_ask(self):
        tc = _make_constructor()
        hyp = _make_hypothesis("BUY")
        indicators = _make_indicators(swing_lows=[{"index": 10, "price": 2730.0}])
        result = tc.construct(hyp, bid=2750.0, ask=2751.0, indicators=indicators, atr=10.0)
        assert result.signal is not None
        entry_mid = (result.signal.entry_zone[0] + result.signal.entry_zone[1]) / 2
        assert entry_mid == pytest.approx(2751.0, abs=0.01)

    def test_sell_uses_bid(self):
        tc = _make_constructor()
        hyp = _make_hypothesis("SELL")
        indicators = _make_indicators(swing_highs=[{"index": 10, "price": 2770.0}])
        result = tc.construct(hyp, bid=2749.0, ask=2750.0, indicators=indicators, atr=10.0)
        assert result.signal is not None
        entry_mid = (result.signal.entry_zone[0] + result.signal.entry_zone[1]) / 2
        assert entry_mid == pytest.approx(2749.0, abs=0.01)


# ── DirectionHypothesis has no SL/TP ─────────────────────────────────────────


class TestDirectionHypothesis:
    def test_no_sl_tp_fields(self):
        """DirectionHypothesis should not have SL/TP fields."""
        hyp = _make_hypothesis()
        assert not hasattr(hyp, "stop_loss")
        assert not hasattr(hyp, "take_profit")
        assert not hasattr(hyp, "entry_zone")
