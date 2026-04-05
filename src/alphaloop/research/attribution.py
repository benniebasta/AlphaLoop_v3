"""
research/attribution.py — Trade P&L factor attribution.

Decomposes trade PnL into four components:
  - pnl_entry_skill  : USD value of entering better/worse than zone midpoint
  - pnl_exit_skill   : R-multiple relative to TP1 (>1.0 = better than TP1)
  - pnl_slippage_usd : USD cost of execution slippage (always ≤ 0)
  - pnl_commission_usd: USD cost of spread/commission (always ≤ 0)

Usage:
    attributor = TradeAttributor()
    attrs = attributor.compute_attribution(trade_dict, pip_value=10.0)
    # {'pnl_entry_skill': 12.5, 'pnl_exit_skill': 1.15, ...}
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class TradeAttributor:
    """
    Computes per-trade P&L attribution into four skill and cost components.

    None is returned for any field where required inputs are missing — this
    allows partial attribution when not all fields are populated.
    """

    def compute_attribution(
        self,
        trade: dict[str, Any],
        pip_value: float = 10.0,
    ) -> dict[str, float | None]:
        """
        Compute attribution components for a single closed trade.

        Parameters
        ----------
        trade : dict
            Must contain: entry_price, close_price, lot_size.
            Optional: entry_zone_low, entry_zone_high, stop_loss,
                      take_profit_1, slippage_points, execution_spread.
        pip_value : float
            USD per pip per lot for this asset (default 10.0 for XAUUSD/EURUSD).

        Returns
        -------
        dict with keys: pnl_entry_skill, pnl_exit_skill,
                        pnl_slippage_usd, pnl_commission_usd
        Each may be None if required inputs are missing.
        """
        entry = _safe_float(trade.get("entry_price"))
        close = _safe_float(trade.get("close_price"))
        lots = _safe_float(trade.get("lot_size"))
        if lots is not None and lots <= 0:
            logger.warning("compute_attribution: lot_size=%s is invalid (must be > 0); attribution skipped", lots)
            lots = None
        direction = (trade.get("direction") or "BUY").upper()
        zone_low = _safe_float(trade.get("entry_zone_low"))
        zone_high = _safe_float(trade.get("entry_zone_high"))
        sl = _safe_float(trade.get("stop_loss"))
        tp1 = _safe_float(trade.get("take_profit_1"))
        slip = _safe_float(trade.get("slippage_points"))
        spread = _safe_float(trade.get("execution_spread"))

        result: dict[str, float | None] = {
            "pnl_entry_skill": None,
            "pnl_exit_skill": None,
            "pnl_slippage_usd": None,
            "pnl_commission_usd": None,
        }

        # ── Entry Skill ─────────────────────────────────────────────────────────
        # Measures how much better/worse than zone midpoint we entered.
        # Positive = entered closer to zone edge (better), negative = worse.
        # Formula (BUY): (zone_midpoint - entry) × lots × pip_value
        #                 positive when entry < midpoint (bought lower = better)
        if entry is not None and zone_low is not None and zone_high is not None and lots is not None:
            try:
                zone_mid = (zone_low + zone_high) / 2
                if direction == "BUY":
                    # Better entry = lower price (bought cheaper)
                    entry_skill_points = zone_mid - entry
                else:
                    # Better entry = higher price (sold more expensive)
                    entry_skill_points = entry - zone_mid
                result["pnl_entry_skill"] = round(entry_skill_points * lots * pip_value, 2)
            except Exception as e:
                logger.debug("Entry skill calc failed: %s", e)

        # ── Exit Skill ──────────────────────────────────────────────────────────
        # R-multiple relative to TP1: how much of the TP1 target was captured.
        # Formula: (close - entry) / (tp1 - entry)  for BUY
        # >1.0 = exited beyond TP1 (runner), 0.0 = exited at entry (breakeven)
        if entry is not None and close is not None and tp1 is not None:
            try:
                if direction == "BUY":
                    move = close - entry
                    target = tp1 - entry
                else:
                    move = entry - close
                    target = entry - tp1
                if abs(target) > 0.0001:  # avoid division by near-zero
                    result["pnl_exit_skill"] = round(move / target, 4)
            except Exception as e:
                logger.debug("Exit skill calc failed: %s", e)

        # ── Slippage USD ────────────────────────────────────────────────────────
        # Always negative (a cost). 0 in dry-run.
        # Formula: -|slippage_points| × lots × pip_value
        if slip is not None and lots is not None:
            try:
                result["pnl_slippage_usd"] = round(-abs(slip) * lots * pip_value, 2)
            except Exception as e:
                logger.debug("Slippage USD calc failed: %s", e)

        # ── Commission / Spread Cost USD ────────────────────────────────────────
        # Half the bid-ask spread applied at entry (approximate one-way cost).
        # Always negative.
        if spread is not None and lots is not None:
            try:
                result["pnl_commission_usd"] = round(-abs(spread) * 0.5 * lots * pip_value, 2)
            except Exception as e:
                logger.debug("Commission USD calc failed: %s", e)

        return result


def _safe_float(value: Any) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        f = float(value)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None
