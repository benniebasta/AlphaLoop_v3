"""
risk/trailing_manager.py — Client-side trailing stop loss manager.

Evaluates open trades each cycle and returns a TrailEvent when the SL
should be ratcheted closer to the current price.

Design rules
------------
- Never widens SL (monotonicity enforced before returning an event).
- Repositioner tighten_sl events always win — the repositioner runs first
  in the same cycle, updating trade.stop_loss in memory before this manager
  is called.
- Activation threshold prevents chasing noise immediately after entry.
- Step filter avoids hammering the broker with tiny SLTP updates.
- State (trail_high_water) is persisted to DB so restarts resume correctly.
- Works identically for algo_only, algo_ai, and ai_signal modes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class TrailingConfig:
    """Validated trailing SL configuration extracted from strategy params."""

    enabled: bool = False
    trail_type: str = "atr"          # "atr" | "fixed_pips"
    trail_atr_mult: float = 1.5      # SL distance = ATR × trail_atr_mult
    trail_pips: float = 200.0        # SL distance in pips (fixed mode)
    activation_rr: float = 1.0       # min R-multiple in profit before trail starts
    step_min_pips: float = 5.0       # min SL improvement per cycle (avoids broker spam)
    pip_size: float = 0.1            # from AssetConfig (symbol-specific)

    @classmethod
    def from_params(cls, params: dict, symbol: str) -> "TrailingConfig":
        """
        Build a TrailingConfig from the strategy runtime params dict.

        Reads trail_enabled from params OR from tools["trailing_stop"] if
        the tool toggle is used (tools dict takes precedence when present).
        """
        # Tool toggle maps to trail_enabled
        tools = params.get("tools") or {}
        tool_on = tools.get("trailing_stop")
        param_enabled = params.get("trail_enabled", False)
        enabled = bool(tool_on) if tool_on is not None else bool(param_enabled)

        # Resolve pip_size and symbol-specific trail defaults from asset config
        pip_size = 0.1
        atr_mult_default = 1.5
        pips_default = 200.0
        activation_rr_default = 1.0
        step_min_pips_default = 5.0
        try:
            from alphaloop.config.assets import get_asset_config
            asset_cfg = get_asset_config(symbol)
            pip_size = asset_cfg.pip_size
            atr_mult_default = asset_cfg.trail_atr_mult
            pips_default = asset_cfg.trail_pips
            activation_rr_default = asset_cfg.trail_activation_rr
            step_min_pips_default = asset_cfg.trail_step_min_pips
        except Exception:
            pass

        return cls(
            enabled=enabled,
            trail_type=str(params.get("trail_type", "atr")).lower(),
            trail_atr_mult=float(params.get("trail_atr_mult", atr_mult_default)),
            trail_pips=float(params.get("trail_pips", pips_default)),
            activation_rr=float(params.get("trail_activation_rr", activation_rr_default)),
            step_min_pips=float(params.get("trail_step_min_pips", step_min_pips_default)),
            pip_size=pip_size,
        )


@dataclass
class TrailEvent:
    """Returned by TrailingStopManager when SL should be moved."""

    new_sl: float
    new_high_water: float
    trail_type: str
    old_sl: float
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TrailingStopManager:
    """
    Stateless evaluator for trailing stop loss.

    Per-trade state (high-water mark) is read from trade.trail_high_water
    and written back by the caller (loop.py) after the event is acted on.
    """

    def evaluate(
        self,
        *,
        trade,
        current_price: float,
        atr: float,
        config: TrailingConfig,
    ) -> TrailEvent | None:
        """
        Evaluate whether the trailing SL should be moved for this trade.

        Parameters
        ----------
        trade       : TradeLog ORM instance (needs .direction, .entry_price,
                      .stop_loss, .trail_high_water)
        current_price : latest bid (BUY) or ask (SELL)
        atr           : current H1 ATR value
        config        : TrailingConfig for this strategy

        Returns
        -------
        TrailEvent if SL should be updated, None otherwise.
        """
        if not config.enabled:
            return None

        direction = (getattr(trade, "direction", "") or "").upper()
        entry_price = float(getattr(trade, "entry_price", 0) or 0)
        current_sl = float(getattr(trade, "stop_loss", 0) or 0)
        high_water = getattr(trade, "trail_high_water", None)

        if not entry_price or not current_sl or direction not in ("BUY", "SELL"):
            return None

        # ── Activation threshold ──────────────────────────────────────────────
        initial_risk = abs(entry_price - current_sl)
        if initial_risk <= 0:
            return None

        if direction == "BUY":
            profit = current_price - entry_price
        else:
            profit = entry_price - current_price

        profit_r = profit / initial_risk
        if profit_r < config.activation_rr:
            return None

        # ── High-water mark ───────────────────────────────────────────────────
        if direction == "BUY":
            new_hw = max(float(high_water or entry_price), current_price)
        else:
            new_hw = min(float(high_water or entry_price), current_price)

        # ── Proposed SL ───────────────────────────────────────────────────────
        if config.trail_type == "fixed_pips":
            trail_dist = config.trail_pips * config.pip_size
            reason = f"fixed {config.trail_pips:.0f}pip trail"
        else:
            if atr <= 0:
                logger.debug("[trail-sl] ATR=0 — skipping trail evaluation")
                return None
            trail_dist = atr * config.trail_atr_mult
            reason = f"ATR-trail {config.trail_atr_mult}×ATR({atr:.5f})"

        if direction == "BUY":
            proposed_sl = new_hw - trail_dist
            # Monotonicity — never widen
            proposed_sl = max(proposed_sl, current_sl)
            improvement = proposed_sl - current_sl
        else:
            proposed_sl = new_hw + trail_dist
            # Monotonicity — never widen
            proposed_sl = min(proposed_sl, current_sl)
            improvement = current_sl - proposed_sl

        # ── Step filter ───────────────────────────────────────────────────────
        min_step = config.step_min_pips * config.pip_size
        if improvement < min_step:
            return None

        return TrailEvent(
            new_sl=round(proposed_sl, 5),
            new_high_water=round(new_hw, 5),
            trail_type=config.trail_type,
            old_sl=current_sl,
            reason=f"{reason} hw={new_hw:.5f} price={current_price:.5f} R={profit_r:.2f}",
        )
