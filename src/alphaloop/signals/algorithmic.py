"""
Deterministic signal generator using EMA crossover + RSI + toggleable filters.

Same algorithm as backtest make_signal_fn(), but works with live market context
(pre-computed indicators from _build_context()) instead of raw numpy arrays.
Produces TradeSignal objects compatible with the validation pipeline.

Used in Mode A (algo_only) and Mode B (algo_plus_ai) of the signal mode system.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from alphaloop.core.types import TrendDirection, SetupType
from alphaloop.signals.schema import TradeSignal

logger = logging.getLogger(__name__)


class AlgorithmicSignalEngine:
    """
    Deterministic signal generator — same logic as backtest make_signal_fn().

    Reads pre-computed indicators from the market context dict and applies
    EMA crossover + RSI confirmation with toggleable tool filters.
    Tools are NOT applied here — they run via the strategy pipeline (Phase 2).
    """

    def __init__(self, symbol: str, params: dict, prev_ema_state: dict | None = None):
        self.symbol = symbol
        self.params = params
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        if prev_ema_state:
            self._prev_fast = prev_ema_state.get("fast")
            self._prev_slow = prev_ema_state.get("slow")

    async def generate_signal(self, context: dict) -> TradeSignal | None:
        """
        Generate a trade signal from market context using EMA crossover + RSI.

        Returns TradeSignal or None if no setup detected.
        Context expected shape: context["timeframes"]["M15"]["indicators"]
        """
        m15 = context.get("timeframes", {}).get("M15", {}).get("indicators", {})
        price_data = context.get("current_price", {})

        ema_fast_val = m15.get("ema21")
        ema_slow_val = m15.get("ema55")
        rsi_val = m15.get("rsi")
        atr_val = m15.get("atr")

        if ema_fast_val is None or ema_slow_val is None or rsi_val is None:
            return None

        try:
            ema_fast = float(ema_fast_val)
            ema_slow = float(ema_slow_val)
            rsi = float(rsi_val)
            atr = float(atr_val) if atr_val else 0
        except (TypeError, ValueError):
            return None

        price = float(price_data.get("bid", 0) or 0)
        if price <= 0 or atr <= 0:
            return None

        # EMA crossover detection (requires previous values)
        rsi_ob = self.params.get("rsi_ob", 70.0)
        rsi_os = self.params.get("rsi_os", 30.0)

        is_cross_up = False
        is_cross_down = False

        if self._prev_fast is not None and self._prev_slow is not None:
            is_cross_up = (
                ema_fast > ema_slow
                and self._prev_fast <= self._prev_slow
                and rsi < rsi_ob
            )
            is_cross_down = (
                ema_fast < ema_slow
                and self._prev_fast >= self._prev_slow
                and rsi > rsi_os
            )

        # Store current for next cycle
        self._prev_fast = ema_fast
        self._prev_slow = ema_slow

        if not is_cross_up and not is_cross_down:
            return None

        direction = TrendDirection.BULLISH if is_cross_up else TrendDirection.BEARISH

        # Build SL/TP from params
        sl_atr_mult = self.params.get("sl_atr_mult", 1.5)
        tp1_rr = self.params.get("tp1_rr", 1.5)
        tp2_rr = self.params.get("tp2_rr", 2.5)

        sl_dist = sl_atr_mult * atr
        tp1_dist = sl_dist * tp1_rr
        tp2_dist = sl_dist * tp2_rr
        zone_mult = self.params.get("entry_zone_atr_mult", 0.25)
        zone_half = atr * zone_mult

        if direction == TrendDirection.BULLISH:
            entry_low = price - zone_half
            entry_high = price + zone_half
            sl = price - sl_dist
            tp1 = price + tp1_dist
            tp2 = price + tp2_dist
        else:
            entry_low = price - zone_half
            entry_high = price + zone_half
            sl = price + sl_dist
            tp1 = price - tp1_dist
            tp2 = price - tp2_dist

        signal = TradeSignal(
            trend=direction,
            setup=SetupType.PULLBACK,
            entry_zone=[round(entry_low, 5), round(entry_high, 5)],
            stop_loss=round(sl, 5),
            take_profit=[round(tp1, 5), round(tp2, 5)],
            confidence=0.75,
            reasoning=f"EMA{self.params.get('ema_fast', 21)}/{self.params.get('ema_slow', 55)} "
                      f"crossover confirmed by RSI({rsi:.0f}). "
                      f"ATR-based SL={sl_dist:.1f}, TP1 RR={tp1_rr}.",
            timeframe="M15",
            generated_at=datetime.now(timezone.utc),
        )

        logger.info(
            "[algo-signal] %s %s conf=%.2f price=%.2f SL=%.2f TP1=%.2f",
            self.symbol, signal.direction, signal.confidence, price, sl, tp1,
        )
        return signal
