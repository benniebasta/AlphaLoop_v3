"""
Deterministic signal generator using composable signal sources + toggleable filters.

Same algorithm as backtest make_signal_fn(), but works with live market context
(pre-computed indicators from _build_context()) instead of raw numpy arrays.
Produces TradeSignal objects compatible with the validation pipeline.

Signal sources are driven by params["signal_rules"] (list of {source}) and
params["signal_logic"] ("AND" | "OR" | "MAJORITY"). Defaults to EMA crossover
for backward compatibility.

Used in the algorithmic signal modes: algo_only and algo_ai.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from alphaloop.core.setup_types import (
    normalize_pipeline_setup_type,
    normalize_schema_setup_type,
)
from alphaloop.core.types import TrendDirection, SetupType
from alphaloop.pipeline.types import DirectionHypothesis
from alphaloop.signals.conditions import (
    check_ema_crossover, check_macd_crossover, check_rsi_reversal,
    check_bollinger, check_adx_trend, check_bos, combine,
)
from alphaloop.signals.schema import TradeSignal
from alphaloop.trading.strategy_loader import (
    resolve_strategy_signal_logic,
    resolve_strategy_signal_rules,
)

logger = logging.getLogger(__name__)

def _normalize_setup_tag(raw: str | None) -> str:
    return normalize_schema_setup_type(raw)


def _normalize_hypothesis_setup_tag(raw: str | None) -> str:
    return normalize_pipeline_setup_type(_normalize_setup_tag(raw))


def _configured_signal_rules(params: dict) -> list[dict]:
    """Resolve signal rules through the shared strategy contract."""
    return resolve_strategy_signal_rules(params, default_to_ema=True)


def _configured_signal_logic(params: dict) -> str:
    return resolve_strategy_signal_logic(params)


# ---------------------------------------------------------------------------
# Pure sync direction helper — usable from both async live and sync backtest
# ---------------------------------------------------------------------------

def compute_direction(
    *,
    signal_rules: list[dict],
    signal_logic: str,
    rsi_ob: float,
    rsi_os: float,
    price: float,
    ema_fast: float | None,
    ema_slow: float | None,
    prev_ema_fast: float | None,
    prev_ema_slow: float | None,
    rsi: float | None,
    macd_hist: float | None,
    prev_macd_hist: float | None,
    bb_pct_b: float | None,
    adx: float | None,
    plus_di: float | None,
    minus_di: float | None,
    prev_rsi: float | None = None,
    adx_min_threshold: float = 20.0,
    bos_swing_high: float | None = None,
    bos_swing_low: float | None = None,
) -> tuple[str, float, str] | None:
    """Compute direction, confidence, and reasoning from indicator values.

    Pure sync function — no state, no I/O. Used by both the live
    :class:`AlgorithmicSignalEngine` (via ``generate_hypothesis``) and
    the vectorbt backtest engine.

    Returns
    -------
    (direction, confidence, reasoning) or None
        direction is ``"BUY"`` or ``"SELL"``.
    """
    rule_results: list[tuple[bool, bool]] = []

    for rule in signal_rules:
        src = rule.get("source", "ema_crossover")

        if src == "ema_crossover":
            if ema_fast is None or ema_slow is None or rsi is None:
                continue
            if prev_ema_fast is None or prev_ema_slow is None:
                continue
            rule_results.append(check_ema_crossover(
                ema_fast, prev_ema_fast,
                ema_slow, prev_ema_slow,
                rsi, rsi_ob, rsi_os,
            ))
        elif src == "macd_crossover":
            if macd_hist is not None and prev_macd_hist is not None:
                rule_results.append(check_macd_crossover(macd_hist, prev_macd_hist))
        elif src == "rsi_reversal":
            if rsi is not None and prev_rsi is not None:
                rule_results.append(check_rsi_reversal(rsi, prev_rsi, rsi_ob, rsi_os))
        elif src == "bollinger_breakout":
            if bb_pct_b is not None:
                rule_results.append(check_bollinger(bb_pct_b))
        elif src == "adx_trend":
            if adx is not None and plus_di is not None and minus_di is not None:
                rule_results.append(check_adx_trend(adx, plus_di, minus_di, adx_min_threshold))
        elif src == "bos_confirm":
            rule_results.append(check_bos(price, bos_swing_high, bos_swing_low))

    if not rule_results:
        return None

    is_bull, is_bear = combine(rule_results, signal_logic)
    if not is_bull and not is_bear:
        return None

    direction = "BUY" if is_bull else "SELL"
    source_names = "+".join(r.get("source", "ema_crossover") for r in signal_rules)

    n_active = max(len(rule_results), 1)
    n_winning = sum(1 for b, bear in rule_results if (is_bull and b) or (not is_bull and bear))
    agreement_ratio = n_winning / n_active
    rsi_factor = 0.0
    if rsi is not None:
        rsi_factor = min(abs(rsi - 50) / 50, 1.0) * 0.08
    base_confidence = 0.55 + (agreement_ratio * 0.25) + rsi_factor
    confidence = round(min(base_confidence, 0.90), 3)

    reasoning = (
        f"{source_names} ({signal_logic}) signal. "
        f"agreement={n_winning}/{n_active}."
    )

    return direction, confidence, reasoning


class AlgorithmicSignalEngine:
    """
    Deterministic signal generator — same logic as backtest make_signal_fn().

    Reads pre-computed indicators from the market context dict and dispatches
    to condition checkers based on params["signal_rules"].
    Tools are NOT applied here — they run via the strategy pipeline (Phase 2).
    """

    def __init__(
        self,
        symbol: str,
        params: dict,
        prev_ema_state: dict | None = None,
        *,
        setup_tag: str = "pullback",
    ):
        self.symbol = symbol
        self.params = params
        self.setup_tag = _normalize_setup_tag(setup_tag)
        # Per-source previous-bar state
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None
        self._prev_macd_hist: float | None = None
        self._prev_rsi: float | None = None
        self._prev_bb_pct_b: float | None = None
        self.last_neutral_reason: str | None = None
        if prev_ema_state:
            self._prev_fast = prev_ema_state.get("fast")
            self._prev_slow = prev_ema_state.get("slow")

    async def generate_signal(self, context: dict) -> TradeSignal | None:
        """
        Generate a trade signal from market context using configured signal rules.

        Returns TradeSignal or None if no setup detected.
        Context expected shape: context["timeframes"]["M15"]["indicators"]
        """
        m15 = context.get("timeframes", {}).get("M15", {}).get("indicators", {})
        price_data = context.get("current_price", {})

        atr_val = m15.get("atr")
        try:
            atr = float(atr_val) if atr_val else 0
        except (TypeError, ValueError):
            atr = 0

        price = float(price_data.get("bid", 0) or 0)
        if price <= 0 or atr <= 0:
            logger.debug(
                "[algo] Price/ATR zero: price=%s atr=%s (price_data keys=%s)",
                price, atr, list(price_data.keys()),
            )
            self.last_neutral_reason = f"Price/ATR missing (price={price} atr={atr}) — check MT5"
            return None

        signal_rules = _configured_signal_rules(self.params)
        signal_logic = _configured_signal_logic(self.params)
        rsi_ob = self.params.get("rsi_ob", 70.0)
        rsi_os = self.params.get("rsi_os", 30.0)

        # Read current indicator values
        ema_fast_val = m15.get("ema_fast")
        ema_slow_val = m15.get("ema_slow")
        rsi_val = m15.get("rsi")
        macd_hist_val = m15.get("macd_histogram")
        bb_pct_b_val = m15.get("bb_pct_b")
        adx_val = m15.get("adx")
        plus_di_val = m15.get("plus_di")
        minus_di_val = m15.get("minus_di")

        try:
            ema_fast = float(ema_fast_val) if ema_fast_val is not None else None
            ema_slow = float(ema_slow_val) if ema_slow_val is not None else None
            rsi = float(rsi_val) if rsi_val is not None else None
            macd_hist = float(macd_hist_val) if macd_hist_val is not None else None
            bb_pct_b = float(bb_pct_b_val) if bb_pct_b_val is not None else None
            adx = float(adx_val) if adx_val is not None else None
            plus_di = float(plus_di_val) if plus_di_val is not None else None
            minus_di = float(minus_di_val) if minus_di_val is not None else None
        except (TypeError, ValueError):
            logger.debug("[algo] M15 parse error")
            self.last_neutral_reason = "M15 indicator parse error — bad data from MT5"
            return None

        logger.debug(
            "[algo] indicators: EMA_fast=%s EMA_slow=%s RSI=%s "
            "MACD_hist=%s BB_pct_b=%s ADX=%s plus_di=%s minus_di=%s "
            "prev_fast=%s prev_slow=%s",
            ema_fast, ema_slow, rsi,
            macd_hist, bb_pct_b, adx, plus_di, minus_di,
            self._prev_fast, self._prev_slow,
        )

        rule_results: list[tuple[bool, bool]] = []

        for rule in signal_rules:
            src = rule.get("source", "ema_crossover")

            if src == "ema_crossover":
                if ema_fast is None or ema_slow is None or rsi is None:
                    logger.debug("[algo] ema_crossover: missing ema_fast/ema_slow/rsi")
                    continue
                if self._prev_fast is None or self._prev_slow is None:
                    # Seed state, no signal this cycle
                    logger.debug(
                        "[algo] ema_crossover: seed state — storing "
                        "EMA_fast=%s EMA_slow=%s, skipping crossover this cycle",
                        ema_fast, ema_slow,
                    )
                    self._prev_fast = ema_fast
                    self._prev_slow = ema_slow
                    continue
                rule_results.append(check_ema_crossover(
                    ema_fast, self._prev_fast,
                    ema_slow, self._prev_slow,
                    rsi, rsi_ob, rsi_os,
                ))
                _bull, _bear = rule_results[-1]
                logger.debug(
                    "[algo] ema_crossover: fast=%.4f prev=%.4f slow=%.4f prev=%.4f "
                    "rsi=%.2f → bull=%s bear=%s",
                    ema_fast, self._prev_fast, ema_slow, self._prev_slow,
                    rsi, _bull, _bear,
                )

            elif src == "macd_crossover":
                if macd_hist is not None and self._prev_macd_hist is not None:
                    rule_results.append(check_macd_crossover(macd_hist, self._prev_macd_hist))
                elif macd_hist is not None:
                    self._prev_macd_hist = macd_hist  # seed
                    continue

            elif src == "rsi_reversal":
                if rsi is not None and self._prev_rsi is not None:
                    rule_results.append(check_rsi_reversal(rsi, self._prev_rsi, rsi_ob, rsi_os))
                elif rsi is not None:
                    self._prev_rsi = rsi  # seed
                    continue

            elif src == "bollinger_breakout":
                if bb_pct_b is not None:
                    rule_results.append(check_bollinger(bb_pct_b))

            elif src == "adx_trend":
                if adx is not None and plus_di is not None and minus_di is not None:
                    rule_results.append(check_adx_trend(
                        adx, plus_di, minus_di,
                        self.params.get("adx_min_threshold", 20.0),
                    ))

            elif src == "bos_confirm":
                bos_data = m15.get("bos") or {}
                swing_h = bos_data.get("last_swing_high")
                swing_l = bos_data.get("last_swing_low")
                rule_results.append(check_bos(price, swing_h, swing_l))

        # Update previous-bar state for next cycle
        if ema_fast is not None:
            self._prev_fast = ema_fast
        if ema_slow is not None:
            self._prev_slow = ema_slow
        if macd_hist is not None:
            self._prev_macd_hist = macd_hist
        if rsi is not None:
            self._prev_rsi = rsi
        if bb_pct_b is not None:
            self._prev_bb_pct_b = bb_pct_b

        if not rule_results:
            logger.debug(
                "[algo] no rule results — all configured rules skipped "
                "(rules=%s). Likely seed state, missing indicators, or "
                "adx_trend missing plus_di/minus_di in context.",
                [r.get("source", "?") for r in signal_rules],
            )
            self.last_neutral_reason = (
                f"Seed state — building baseline "
                f"(EMA fast={ema_fast} slow={ema_slow} RSI={rsi})"
            )
            return None

        is_bull, is_bear = combine(rule_results, signal_logic)
        if not is_bull and not is_bear:
            logger.debug(
                "[algo] combine(%d rules, logic=%s) → no directional signal "
                "(rule_results=%s)",
                len(rule_results), signal_logic, rule_results,
            )
            self.last_neutral_reason = (
                f"No crossover: EMA fast={ema_fast:.2f} slow={ema_slow:.2f} "
                f"RSI={rsi:.1f}" if (ema_fast and ema_slow and rsi) else "No directional agreement"
            )
            return None

        direction = TrendDirection.BULLISH if is_bull else TrendDirection.BEARISH

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

        source_names = "+".join(r.get("source", "ema_crossover") for r in signal_rules)

        # Dynamic confidence based on rule agreement strength and RSI clarity
        # - agreement_ratio: fraction of active rules that fired in the winning direction
        # - rsi_clearance:   how far RSI is from the 50 midpoint (cleaner signal = more confident)
        n_active = max(len(rule_results), 1)
        n_winning = sum(1 for b, bear in rule_results if (is_bull and b) or (not is_bull and bear))
        agreement_ratio = n_winning / n_active
        rsi_factor = 0.0
        if rsi is not None:
            rsi_factor = min(abs(rsi - 50) / 50, 1.0) * 0.08   # up to +0.08 bonus
        base_confidence = 0.55 + (agreement_ratio * 0.25) + rsi_factor
        computed_confidence = round(min(base_confidence, 0.90), 3)

        signal = TradeSignal(
            trend=direction,
            setup=SetupType(self.setup_tag) if self.setup_tag in {member.value for member in SetupType} else SetupType.PULLBACK,
            entry_zone=[round(entry_low, 5), round(entry_high, 5)],
            stop_loss=round(sl, 5),
            take_profit=[round(tp1, 5), round(tp2, 5)],
            confidence=computed_confidence,
            reasoning=(
                f"{source_names} ({signal_logic}) signal. "
                f"agreement={n_winning}/{n_active} ATR-SL={sl_dist:.4f} RR={tp1_rr}."
            ),
            timeframe="M15",
            generated_at=datetime.now(timezone.utc),
        )

        self.last_neutral_reason = None
        logger.info(
            "[algo-signal] %s %s src=%s conf=%.2f price=%.5f SL=%.5f TP1=%.5f",
            self.symbol, signal.direction, source_names, signal.confidence, price, sl, tp1,
        )
        return signal

    # ------------------------------------------------------------------
    # Constraint-first: direction-only hypothesis (no SL/TP)
    # ------------------------------------------------------------------

    async def generate_hypothesis(self, context: dict) -> DirectionHypothesis | None:
        """Generate a direction hypothesis without SL/TP.

        Reuses the same indicator reading, rule dispatch, and confidence
        calculation as :meth:`generate_signal` but does NOT compute
        SL/TP.  Price-level construction is handled downstream by
        :class:`~alphaloop.pipeline.construction.TradeConstructor`.
        """
        m15 = context.get("timeframes", {}).get("M15", {}).get("indicators", {})
        price_data = context.get("current_price", {})

        atr_val = m15.get("atr")
        try:
            atr = float(atr_val) if atr_val else 0
        except (TypeError, ValueError):
            atr = 0

        price = float(price_data.get("bid", 0) or 0)
        if price <= 0 or atr <= 0:
            self.last_neutral_reason = f"Price/ATR missing (price={price} atr={atr}) — check MT5"
            return None

        signal_rules = _configured_signal_rules(self.params)
        signal_logic = _configured_signal_logic(self.params)
        rsi_ob = self.params.get("rsi_ob", 70.0)
        rsi_os = self.params.get("rsi_os", 30.0)

        # --- Read indicators (identical to generate_signal) ---
        ema_fast_val = m15.get("ema_fast")
        ema_slow_val = m15.get("ema_slow")
        rsi_val = m15.get("rsi")
        macd_hist_val = m15.get("macd_histogram")
        bb_pct_b_val = m15.get("bb_pct_b")
        adx_val = m15.get("adx")
        plus_di_val = m15.get("plus_di")
        minus_di_val = m15.get("minus_di")

        try:
            ema_fast = float(ema_fast_val) if ema_fast_val is not None else None
            ema_slow = float(ema_slow_val) if ema_slow_val is not None else None
            rsi = float(rsi_val) if rsi_val is not None else None
            macd_hist = float(macd_hist_val) if macd_hist_val is not None else None
            bb_pct_b = float(bb_pct_b_val) if bb_pct_b_val is not None else None
            adx = float(adx_val) if adx_val is not None else None
            plus_di = float(plus_di_val) if plus_di_val is not None else None
            minus_di = float(minus_di_val) if minus_di_val is not None else None
        except (TypeError, ValueError):
            self.last_neutral_reason = "M15 indicator parse error — bad data from MT5"
            return None

        # --- Dispatch rules (identical to generate_signal) ---
        rule_results: list[tuple[bool, bool]] = []

        for rule in signal_rules:
            src = rule.get("source", "ema_crossover")

            if src == "ema_crossover":
                if ema_fast is None or ema_slow is None or rsi is None:
                    continue
                if self._prev_fast is None or self._prev_slow is None:
                    self._prev_fast = ema_fast
                    self._prev_slow = ema_slow
                    continue
                rule_results.append(check_ema_crossover(
                    ema_fast, self._prev_fast,
                    ema_slow, self._prev_slow,
                    rsi, rsi_ob, rsi_os,
                ))
            elif src == "macd_crossover":
                if macd_hist is not None and self._prev_macd_hist is not None:
                    rule_results.append(check_macd_crossover(macd_hist, self._prev_macd_hist))
                elif macd_hist is not None:
                    self._prev_macd_hist = macd_hist
                    continue
            elif src == "rsi_reversal":
                if rsi is not None and self._prev_rsi is not None:
                    rule_results.append(check_rsi_reversal(rsi, self._prev_rsi, rsi_ob, rsi_os))
                elif rsi is not None:
                    self._prev_rsi = rsi
                    continue
            elif src == "bollinger_breakout":
                if bb_pct_b is not None:
                    rule_results.append(check_bollinger(bb_pct_b))
            elif src == "adx_trend":
                if adx is not None and plus_di is not None and minus_di is not None:
                    rule_results.append(check_adx_trend(
                        adx, plus_di, minus_di,
                        self.params.get("adx_min_threshold", 20.0),
                    ))
            elif src == "bos_confirm":
                bos_data = m15.get("bos") or {}
                swing_h = bos_data.get("last_swing_high")
                swing_l = bos_data.get("last_swing_low")
                rule_results.append(check_bos(price, swing_h, swing_l))

        # Update state for next cycle
        if ema_fast is not None:
            self._prev_fast = ema_fast
        if ema_slow is not None:
            self._prev_slow = ema_slow
        if macd_hist is not None:
            self._prev_macd_hist = macd_hist
        if rsi is not None:
            self._prev_rsi = rsi
        if bb_pct_b is not None:
            self._prev_bb_pct_b = bb_pct_b

        if not rule_results:
            self.last_neutral_reason = (
                f"Seed state — building baseline "
                f"(EMA fast={ema_fast} slow={ema_slow} RSI={rsi})"
            )
            return None

        is_bull, is_bear = combine(rule_results, signal_logic)
        if not is_bull and not is_bear:
            self.last_neutral_reason = (
                f"No crossover: EMA fast={ema_fast:.2f} slow={ema_slow:.2f} "
                f"RSI={rsi:.1f}" if (ema_fast and ema_slow and rsi)
                else "No directional agreement"
            )
            return None

        direction = TrendDirection.BULLISH if is_bull else TrendDirection.BEARISH
        direction_str = "BUY" if is_bull else "SELL"
        source_names = "+".join(r.get("source", "ema_crossover") for r in signal_rules)

        # Confidence (identical to generate_signal)
        n_active = max(len(rule_results), 1)
        n_winning = sum(1 for b, bear in rule_results if (is_bull and b) or (not is_bull and bear))
        agreement_ratio = n_winning / n_active
        rsi_factor = 0.0
        if rsi is not None:
            rsi_factor = min(abs(rsi - 50) / 50, 1.0) * 0.08
        base_confidence = 0.55 + (agreement_ratio * 0.25) + rsi_factor
        computed_confidence = round(min(base_confidence, 0.90), 3)

        self.last_neutral_reason = None
        logger.info(
            "[algo-hypothesis] %s %s src=%s conf=%.2f",
            self.symbol, direction_str, source_names, computed_confidence,
        )

        return DirectionHypothesis(
            direction=direction_str,
            confidence=computed_confidence,
            setup_tag=_normalize_hypothesis_setup_tag(self.setup_tag),
            reasoning=(
                f"{source_names} ({signal_logic}) signal. "
                f"agreement={n_winning}/{n_active}."
            ),
            source_names=source_names,
            generated_at=datetime.now(timezone.utc),
        )
