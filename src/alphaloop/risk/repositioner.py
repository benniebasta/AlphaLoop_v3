"""
Dynamic trade repositioning — evaluated every cycle for each open trade.

Triggers: opposite_signal, news_risk, volume_spike, volatility_spike
Actions: tighten_sl, partial_close, full_close
"""

import logging
from datetime import datetime, timezone

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RepositionEvent(BaseModel):
    """Describes a repositioning action for an open trade."""
    trigger: str
    action: str  # tighten_sl | partial_close | full_close
    reason: str
    old_sl: float
    old_tp: float
    new_sl: float | None = None
    new_tp: float | None = None
    lots_closed: float | None = None
    close_price: float | None = None
    timestamp: str = ""

    def model_post_init(self, __context) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class TradeRepositioner:
    """
    Evaluates open trades for repositioning triggers each loop cycle.
    """

    def check(
        self,
        trade_info: dict,
        context: dict,
        *,
        new_signal=None,
        current_price: float | None = None,
    ) -> list[RepositionEvent]:
        """Returns a list of RepositionEvents to apply."""
        order = trade_info.get("order_result", {})
        direction = order.get("direction", "BUY") if isinstance(order, dict) else getattr(order, "direction", "BUY")
        entry = order.get("entry_price", 0) if isinstance(order, dict) else getattr(order, "entry_price", 0)
        sl = order.get("sl", 0) if isinstance(order, dict) else getattr(order, "sl", 0)
        tp = order.get("tp1", 0) if isinstance(order, dict) else getattr(order, "tp1", 0)
        mid = current_price or entry

        events: list[RepositionEvent] = []

        # Opposite signal -> full close
        if new_signal is not None:
            ev = self._opposite_signal(direction, entry, sl, tp, new_signal)
            if ev:
                ev.close_price = mid
                return [ev]

        # News risk
        ev = self._news_risk(direction, entry, sl, tp, mid, context)
        if ev:
            events.append(ev)

        # Volume spike
        if not any(e.trigger == "news_risk" for e in events):
            ev = self._volume_spike(direction, entry, sl, tp, mid, context)
            if ev:
                events.append(ev)

        # Volatility spike
        ev = self._volatility_spike(direction, entry, sl, tp, mid, context)
        if ev:
            events.append(ev)

        return events

    def _opposite_signal(
        self, direction: str, entry: float, sl: float, tp: float, signal
    ) -> RepositionEvent | None:
        sig_dir = signal.original.direction if hasattr(signal, "original") else signal.direction
        if sig_dir == direction:
            return None
        return RepositionEvent(
            trigger="opposite_signal",
            action="full_close",
            reason=f"New {sig_dir} signal conflicts with open {direction}",
            old_sl=sl,
            old_tp=tp,
        )

    def _news_risk(
        self, direction: str, entry: float, sl: float, tp: float,
        mid: float, context: dict,
    ) -> RepositionEvent | None:
        upcoming = context.get("upcoming_news", [])
        for event in upcoming:
            if event.get("impact") not in ("HIGH", "CRITICAL"):
                continue
            try:
                from datetime import datetime as dt, timezone as tz
                event_time = dt.fromisoformat(event["time"])
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=tz.utc)
                delta_min = (event_time - dt.now(tz.utc)).total_seconds() / 60
                if 0 < delta_min <= 15:
                    in_profit = (mid > entry) if direction == "BUY" else (mid < entry)
                    if in_profit:
                        return RepositionEvent(
                            trigger="news_risk",
                            action="tighten_sl",
                            reason=f"News in {delta_min:.0f}m — tightening SL to BE",
                            old_sl=sl,
                            old_tp=tp,
                            new_sl=entry,
                        )
                    else:
                        return RepositionEvent(
                            trigger="news_risk",
                            action="partial_close",
                            reason=f"News in {delta_min:.0f}m — partial close (in loss)",
                            old_sl=sl,
                            old_tp=tp,
                        )
            except (KeyError, ValueError):
                continue
        return None

    def _volume_spike(
        self, direction: str, entry: float, sl: float, tp: float,
        mid: float, context: dict,
    ) -> RepositionEvent | None:
        m5_ind = context.get("timeframes", {}).get("M5", {}).get("indicators", {})
        vol_ratio = m5_ind.get("volume_ratio")
        if vol_ratio is not None and vol_ratio >= 2.5:
            in_profit = (mid > entry) if direction == "BUY" else (mid < entry)
            if in_profit:
                return RepositionEvent(
                    trigger="volume_spike",
                    action="tighten_sl",
                    reason=f"Volume spike {vol_ratio:.1f}x — tightening SL",
                    old_sl=sl,
                    old_tp=tp,
                    new_sl=entry,
                )
        return None

    def _volatility_spike(
        self, direction: str, entry: float, sl: float, tp: float,
        mid: float, context: dict,
    ) -> RepositionEvent | None:
        h1_ind = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
        atr = h1_ind.get("atr")
        baseline = h1_ind.get("atr_baseline")
        if atr and baseline and baseline > 0:
            ratio = atr / baseline
            if ratio >= 1.8:
                in_profit = (mid > entry) if direction == "BUY" else (mid < entry)
                if in_profit:
                    return RepositionEvent(
                        trigger="volatility_spike",
                        action="tighten_sl",
                        reason=f"ATR spike {ratio:.1f}x baseline — tightening SL",
                        old_sl=sl,
                        old_tp=tp,
                        new_sl=entry,
                    )
        return None
