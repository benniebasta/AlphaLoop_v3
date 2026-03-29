"""
Hard-coded validation rules that run before any AI API call.
Non-negotiable filters — fast, deterministic, no external calls.
"""

import logging
from datetime import datetime, timezone

from alphaloop.config.assets import get_asset_config
from alphaloop.signals.schema import TradeSignal

logger = logging.getLogger(__name__)


class HardRuleChecker:
    """
    Runs fast deterministic checks on a signal before AI validation.
    Returns a list of failure reasons (empty = all passed).
    """

    def __init__(self, symbol: str | None = None):
        self._symbol = symbol

    def check(
        self,
        signal: TradeSignal,
        context: dict,
        *,
        cfg: dict | None = None,
    ) -> list[str]:
        """Run all hard rule checks. cfg overrides global thresholds."""
        c = cfg or {}
        failures: list[str] = []

        rules = [
            ("confidence", self._check_confidence(signal, c)),
            ("sl_tp_dir", self._check_sl_tp_direction(signal)),
            ("sl_distance", self._check_sl_distance(signal, c)),
            ("rr_ratio", self._check_rr_ratio(signal, c)),
            ("session", self._check_session(context, c)),
            ("spread", self._check_spread(context, c)),
            ("rsi_extreme", self._check_rsi_extreme(signal, context, c)),
            ("ema200_trend", self._check_ema200_trend(signal, context, c)),
            ("news_blackout", self._check_news_blackout(context, c)),
            ("tick_jump", self._check_tick_jump(context, c)),
            ("liq_vacuum", self._check_liq_vacuum(context, c)),
            ("setup_type", self._check_setup_type(signal, c)),
            ("regime_block", self._check_regime_block(context, c)),
        ]
        for rule_name, (fails, detail) in rules:
            if fails:
                logger.info("[hard-rule] FAIL %s: %s", rule_name, fails[0])
                failures += [f"[{rule_name}] {r}" for r in fails]
            else:
                logger.debug("[hard-rule] PASS %s: %s", rule_name, detail)

        return failures

    # ── Individual rules ─────────────────────────────────────────────────────

    @staticmethod
    def _check_confidence(signal: TradeSignal, c: dict) -> tuple[list[str], str]:
        threshold = c.get("min_confidence", 0.70)
        if signal.confidence < threshold:
            return [f"Confidence {signal.confidence:.2f} < minimum {threshold}"], ""
        return [], f"conf={signal.confidence:.2f} >= min={threshold}"

    @staticmethod
    def _check_sl_tp_direction(signal: TradeSignal) -> tuple[list[str], str]:
        entry = signal.entry_mid
        sl = signal.stop_loss
        tp1 = signal.take_profit[0] if signal.take_profit else None

        if signal.direction == "BUY":
            if sl >= entry:
                return [f"BUY SL {sl} >= entry {entry}"], ""
            if tp1 is not None and tp1 <= entry:
                return [f"BUY TP1 {tp1} <= entry {entry}"], ""
        elif signal.direction == "SELL":
            if sl <= entry:
                return [f"SELL SL {sl} <= entry {entry}"], ""
            if tp1 is not None and tp1 >= entry:
                return [f"SELL TP1 {tp1} >= entry {entry}"], ""
        return [], f"SL/TP valid for {signal.direction}"

    def _check_sl_distance(self, signal: TradeSignal, c: dict) -> tuple[list[str], str]:
        sym = self._symbol or "XAUUSD"
        asset = get_asset_config(sym)
        sl_pts = abs(signal.entry_mid - signal.stop_loss) / asset.pip_size
        if sl_pts < asset.sl_min_points:
            return [f"SL distance {sl_pts:.1f} pts < min {asset.sl_min_points}"], ""
        if sl_pts > asset.sl_max_points:
            return [f"SL distance {sl_pts:.1f} pts > max {asset.sl_max_points}"], ""
        return [], f"SL distance {sl_pts:.1f} pts OK"

    @staticmethod
    def _check_rr_ratio(signal: TradeSignal, c: dict) -> tuple[list[str], str]:
        rr = signal.rr_ratio_tp1
        if rr is None:
            return ["Cannot compute R:R — missing TP"], ""
        threshold = c.get("min_rr", 1.5)
        if rr < threshold:
            return [f"R:R {rr:.2f} < minimum {threshold}"], ""
        return [], f"R:R={rr:.2f} >= min={threshold}"

    @staticmethod
    def _check_session(context: dict, c: dict) -> tuple[list[str], str]:
        session = context.get("session", {})
        score = session.get("score", 0)
        name = session.get("name", "unknown")
        threshold = c.get("min_session_score", 0.50)
        if name == "weekend":
            return ["No trading on weekends"], ""
        if score < threshold:
            return [f"Session '{name}' score {score:.2f} < min {threshold:.2f}"], ""
        return [], f"session={name} score={score:.2f}"

    @staticmethod
    def _check_spread(context: dict, c: dict) -> tuple[list[str], str]:
        spread = context.get("current_price", {}).get("spread", 0)
        if spread is not None and spread <= 0:
            return [], f"spread={spread} (suspicious, skipped)"
        max_spread = c.get("max_spread_points", 50)
        if spread > max_spread:
            return [f"Spread {spread} pts > max {max_spread}"], ""
        return [], f"spread={spread} OK"

    @staticmethod
    def _check_rsi_extreme(signal: TradeSignal, context: dict, c: dict) -> tuple[list[str], str]:
        if not c.get("check_rsi", True):
            return [], "skipped"
        h1_ind = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
        rsi = h1_ind.get("rsi")
        if rsi is None:
            return [], "RSI unavailable"
        ob = c.get("rsi_ob", 75.0)
        os_ = c.get("rsi_os", 25.0)
        if signal.direction == "BUY" and rsi > ob:
            return [f"RSI {rsi:.1f} overbought (>{ob})"], ""
        if signal.direction == "SELL" and rsi < os_:
            return [f"RSI {rsi:.1f} oversold (<{os_})"], ""
        return [], f"RSI={rsi:.1f} OK"

    @staticmethod
    def _check_ema200_trend(signal: TradeSignal, context: dict, c: dict) -> tuple[list[str], str]:
        if not c.get("check_ema200_trend", True):
            return [], "skipped"
        h1_ind = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
        ema200 = h1_ind.get("ema200")
        if ema200 is None:
            return ["EMA200 unavailable — blocking trade"], ""
        price = context.get("current_price", {}).get("bid") or h1_ind.get("last_close")
        if price is None:
            return [], "price unavailable"
        if signal.direction == "BUY" and price < ema200:
            return [f"BUY but price {price:.5g} < EMA200 {ema200:.5g}"], ""
        if signal.direction == "SELL" and price > ema200:
            return [f"SELL but price {price:.5g} > EMA200 {ema200:.5g}"], ""
        return [], f"EMA200 aligned for {signal.direction}"

    @staticmethod
    def _check_news_blackout(context: dict, c: dict) -> tuple[list[str], str]:
        if not c.get("check_news", True):
            return [], "skipped"
        upcoming = context.get("upcoming_news", [])
        now = datetime.now(timezone.utc)
        blackout_before = c.get("avoid_pre_news_minutes", 30) * 60
        blackout_after = c.get("avoid_post_news_minutes", 15) * 60

        for event in upcoming:
            if event.get("impact") not in ("HIGH", "CRITICAL"):
                continue
            try:
                event_time = datetime.fromisoformat(event["time"])
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                event_time = event_time.astimezone(timezone.utc)
                delta = (event_time - now).total_seconds()
                if -blackout_after <= delta <= blackout_before:
                    return [
                        f"News blackout: [{event['impact']}] {event.get('name')} "
                        f"at {event.get('time')} (delta {delta/60:.0f}m)"
                    ], ""
            except (KeyError, ValueError):
                continue
        return [], "no blackout"

    # ── Rules ported from v1 ─────────────────────────────────────────────────

    @staticmethod
    def _check_tick_jump(context: dict, c: dict) -> tuple[list[str], str]:
        """Reject if last 2 bars moved >threshold× ATR (spike entry)."""
        if not c.get("check_tick_jump", True):
            return [], "skipped"
        m15 = context.get("timeframes", {}).get("M15", {})
        closes = m15.get("closes")
        indicators = m15.get("indicators", {})
        atr = indicators.get("atr")
        if closes is None or atr is None or len(closes) < 3 or atr <= 0:
            return [], "tick_jump data unavailable"
        max_mult = c.get("tick_jump_atr_max", 0.8)
        move = abs(closes[-1] - closes[-3])
        ratio = move / atr
        if ratio > max_mult:
            return [f"Tick jump {ratio:.2f}× ATR > max {max_mult}× (2-bar spike)"], ""
        return [], f"tick_jump={ratio:.2f}× OK"

    @staticmethod
    def _check_liq_vacuum(context: dict, c: dict) -> tuple[list[str], str]:
        """Reject ATR spike with small candle body (thin-body spike = liquidity vacuum)."""
        if not c.get("check_liq_vacuum", True):
            return [], "skipped"
        m15 = context.get("timeframes", {}).get("M15", {})
        indicators = m15.get("indicators", {})
        atr = indicators.get("atr")
        last_bar = m15.get("last_bar", {})
        bar_open = last_bar.get("open")
        bar_close = last_bar.get("close")
        bar_high = last_bar.get("high")
        bar_low = last_bar.get("low")
        if None in (atr, bar_open, bar_close, bar_high, bar_low) or atr <= 0:
            return [], "liq_vacuum data unavailable"
        bar_range = bar_high - bar_low
        if bar_range <= 0:
            return [], "zero range bar"
        body = abs(bar_open - bar_close)
        body_pct = (body / bar_range) * 100
        spike_mult = c.get("liq_vacuum_spike_mult", 2.5)
        body_threshold = c.get("liq_vacuum_body_pct", 30)
        if bar_range / atr > spike_mult and body_pct < body_threshold:
            return [
                f"Liquidity vacuum: range={bar_range/atr:.1f}× ATR, "
                f"body={body_pct:.0f}% < {body_threshold}%"
            ], ""
        return [], f"liq_vacuum OK (range={bar_range/atr:.1f}×, body={body_pct:.0f}%)"

    @staticmethod
    def _check_setup_type(signal: TradeSignal, c: dict) -> tuple[list[str], str]:
        """Reject discouraged setup types (e.g. breakout_chase)."""
        if not c.get("check_setup_type", True):
            return [], "skipped"
        blocked = c.get("blocked_setup_types", ["breakout_chase"])
        if isinstance(blocked, str):
            blocked = [blocked]
        setup = getattr(signal, "setup_type", None) or getattr(signal, "setup", None)
        if setup and str(setup).lower() in [b.lower() for b in blocked]:
            return [f"Setup type '{setup}' is blocked"], ""
        return [], f"setup_type={setup} OK"

    @staticmethod
    def _check_regime_block(context: dict, c: dict) -> tuple[list[str], str]:
        """Block all trades in 'dead' market regime (ATR too thin to trade)."""
        if not c.get("check_regime", True):
            return [], "skipped"
        h1 = context.get("timeframes", {}).get("H1", {})
        indicators = h1.get("indicators", {})
        regime = indicators.get("regime") or context.get("regime")
        if regime and str(regime).lower() == "dead":
            return ["Market regime is 'dead' (ATR too thin) — all trading blocked"], ""
        # Also check via ATR ratio if regime not explicitly set
        atr = indicators.get("atr")
        atr_baseline = indicators.get("atr_baseline") or indicators.get("atr_20d")
        if atr is not None and atr_baseline is not None and atr_baseline > 0:
            dead_threshold = c.get("regime_dead_atr_ratio", 0.3)
            ratio = atr / atr_baseline
            if ratio < dead_threshold:
                return [
                    f"Dead regime: ATR ratio {ratio:.2f} < {dead_threshold} "
                    f"(current ATR too thin vs baseline)"
                ], ""
        return [], f"regime OK (regime={regime})"
