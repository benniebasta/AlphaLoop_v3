"""Unit tests for TrailingStopManager."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from alphaloop.risk.trailing_manager import TrailingConfig, TrailingStopManager


def _trade(
    direction="BUY",
    entry_price=2000.0,
    stop_loss=1980.0,
    trail_high_water=None,
):
    return SimpleNamespace(
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        trail_high_water=trail_high_water,
    )


def _cfg(**kwargs):
    base = dict(
        enabled=True,
        trail_type="atr",
        trail_atr_mult=1.5,
        trail_pips=200.0,
        activation_rr=1.0,
        step_min_pips=5.0,
        pip_size=0.1,
    )
    base.update(kwargs)
    return TrailingConfig(**base)


mgr = TrailingStopManager()


# ── ATR trail — BUY ──────────────────────────────────────────────────────────

class TestATRTrailBUY:
    def test_no_event_before_activation(self):
        """Trail should not fire until profit ≥ activation_rr × initial_risk."""
        cfg = _cfg(activation_rr=1.0)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0)  # risk = 20
        # price at 2010 = 0.5R profit — below 1.0R threshold
        ev = mgr.evaluate(trade=trade, current_price=2010.0, atr=10.0, config=cfg)
        assert ev is None

    def test_event_after_activation(self):
        """Trail should fire once profit ≥ activation_rr."""
        cfg = _cfg(activation_rr=1.0, trail_atr_mult=1.5, step_min_pips=5.0, pip_size=0.1)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0)  # risk = 20
        # price at 2021 = 1.05R — above threshold
        # proposed_sl = 2021 - (10 * 1.5) = 2021 - 15 = 2006
        # improvement = 2006 - 1980 = 26 pips → passes step filter (5 pip min)
        ev = mgr.evaluate(trade=trade, current_price=2021.0, atr=10.0, config=cfg)
        assert ev is not None
        assert ev.new_sl == pytest.approx(2006.0, abs=0.001)
        assert ev.new_high_water == pytest.approx(2021.0, abs=0.001)
        assert ev.trail_type == "atr"

    def test_monotonicity_never_widens(self):
        """After a repositioner BE-move, trail must not widen SL below current stop_loss."""
        cfg = _cfg(activation_rr=0.5, trail_atr_mult=5.0)  # aggressive mult → would widen
        # SL already at breakeven (entry price) from repositioner
        trade = _trade(entry_price=2000.0, stop_loss=2000.0)
        # proposed_sl = 2020 - (10 * 5) = 1970 — below current SL 2000
        # monotonicity: max(1970, 2000) = 2000 — no improvement → step filter skips
        ev = mgr.evaluate(trade=trade, current_price=2020.0, atr=10.0, config=cfg)
        assert ev is None  # no improvement after monotonicity clamp

    def test_step_filter_skips_tiny_moves(self):
        """No event if SL improvement is less than step_min_pips."""
        cfg = _cfg(activation_rr=1.0, trail_atr_mult=1.5, step_min_pips=500.0, pip_size=0.1)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0)
        # price = 2025 (1.25R), proposed_sl = 2025 - 15 = 2010
        # improvement = 2010 - 1980 = 30 pts; min_step = 500 pips × 0.1 = 50 pts → 30 < 50 → skip
        ev = mgr.evaluate(trade=trade, current_price=2025.0, atr=10.0, config=cfg)
        assert ev is None

    def test_high_water_ratchets_up(self):
        """High-water mark advances when price sets a new high."""
        cfg = _cfg(activation_rr=1.0, trail_atr_mult=1.0, step_min_pips=1.0)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0, trail_high_water=2030.0)
        # Price at 2040 → new high; sl = 2040 - (10 * 1.0) = 2030
        ev = mgr.evaluate(trade=trade, current_price=2040.0, atr=10.0, config=cfg)
        assert ev is not None
        assert ev.new_high_water == pytest.approx(2040.0, abs=0.001)

    def test_no_event_when_price_pulls_back(self):
        """When price pulls back from high-water, SL should not change."""
        cfg = _cfg(activation_rr=1.0, trail_atr_mult=1.0, step_min_pips=1.0)
        # High water is 2050, SL already moved to 2040
        trade = _trade(entry_price=2000.0, stop_loss=2040.0, trail_high_water=2050.0)
        # Price pulls back to 2035 → proposed_sl = 2050 - 10 = 2040 = current_sl → no improvement
        ev = mgr.evaluate(trade=trade, current_price=2035.0, atr=10.0, config=cfg)
        assert ev is None


# ── Fixed-pips trail — BUY ───────────────────────────────────────────────────

class TestFixedPipsTrailBUY:
    def test_fixed_pips_event(self):
        """Fixed-pip trail places SL at new_high - trail_pips * pip_size."""
        cfg = _cfg(trail_type="fixed_pips", trail_pips=100.0, pip_size=0.1,
                   activation_rr=1.0, step_min_pips=5.0)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0)
        # price = 2021 (1.05R); sl = 2021 - 100 * 0.1 = 2021 - 10 = 2011
        ev = mgr.evaluate(trade=trade, current_price=2021.0, atr=0.0, config=cfg)
        assert ev is not None
        assert ev.new_sl == pytest.approx(2011.0, abs=0.001)
        assert ev.trail_type == "fixed_pips"

    def test_no_event_before_activation_fixed(self):
        cfg = _cfg(trail_type="fixed_pips", trail_pips=50.0, pip_size=0.1,
                   activation_rr=1.0, step_min_pips=1.0)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0)
        # price at 2010 = 0.5R — below 1.0R threshold
        ev = mgr.evaluate(trade=trade, current_price=2010.0, atr=0.0, config=cfg)
        assert ev is None


# ── SELL direction ────────────────────────────────────────────────────────────

class TestSELLDirection:
    def test_sell_trail_moves_sl_down(self):
        """For SELL, SL should move down as price falls."""
        cfg = _cfg(activation_rr=1.0, trail_atr_mult=1.5, step_min_pips=1.0, pip_size=0.1)
        # Entry 2000, SL 2020 (risk = 20)
        trade = _trade(direction="SELL", entry_price=2000.0, stop_loss=2020.0)
        # price at 1979 = 1.05R profit
        # new_low = 1979; proposed_sl = 1979 + (10 * 1.5) = 1979 + 15 = 1994
        # monotonicity: min(1994, 2020) = 1994 ← improvement
        ev = mgr.evaluate(trade=trade, current_price=1979.0, atr=10.0, config=cfg)
        assert ev is not None
        assert ev.new_sl < trade.stop_loss  # SL moved down (better for SELL)
        assert ev.new_sl == pytest.approx(1994.0, abs=0.001)

    def test_sell_monotonicity_never_widens(self):
        """For SELL, proposed_sl must not go above current stop_loss."""
        cfg = _cfg(activation_rr=0.5, trail_atr_mult=10.0, step_min_pips=1.0)
        # SL already at 2005 (below entry 2000 for SELL)
        trade = _trade(direction="SELL", entry_price=2000.0, stop_loss=2005.0)
        # proposed_sl = 1990 + (10*10) = 2090 — above current 2005 → widening
        # monotonicity: min(2090, 2005) = 2005 → no improvement
        ev = mgr.evaluate(trade=trade, current_price=1990.0, atr=10.0, config=cfg)
        assert ev is None


# ── Disabled / edge cases ─────────────────────────────────────────────────────

class TestEdgeCases:
    def test_disabled_returns_none(self):
        cfg = _cfg(enabled=False)
        trade = _trade()
        ev = mgr.evaluate(trade=trade, current_price=2050.0, atr=10.0, config=cfg)
        assert ev is None

    def test_zero_atr_returns_none_for_atr_mode(self):
        cfg = _cfg(trail_type="atr", activation_rr=0.5)
        trade = _trade(entry_price=2000.0, stop_loss=1980.0)
        ev = mgr.evaluate(trade=trade, current_price=2021.0, atr=0.0, config=cfg)
        assert ev is None

    def test_missing_entry_price_returns_none(self):
        cfg = _cfg()
        trade = _trade(entry_price=0.0, stop_loss=1980.0)
        ev = mgr.evaluate(trade=trade, current_price=2021.0, atr=10.0, config=cfg)
        assert ev is None

    def test_from_params_reads_tool_toggle(self):
        """TrailingConfig.from_params should respect tools['trailing_stop'] toggle."""
        params = {"tools": {"trailing_stop": True}, "trail_atr_mult": 2.0}
        cfg = TrailingConfig.from_params(params, "XAUUSD")
        assert cfg.enabled is True
        assert cfg.trail_atr_mult == 2.0

    def test_from_params_tool_off(self):
        params = {"tools": {"trailing_stop": False}, "trail_enabled": True}
        cfg = TrailingConfig.from_params(params, "XAUUSD")
        assert cfg.enabled is False  # tool toggle takes precedence

    def test_from_params_fallback_to_trail_enabled(self):
        params = {"trail_enabled": True, "trail_type": "fixed_pips"}
        cfg = TrailingConfig.from_params(params, "XAUUSD")
        assert cfg.enabled is True
        assert cfg.trail_type == "fixed_pips"
