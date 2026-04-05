"""
Pure signal condition checkers — stateless.

Each function takes pre-computed scalar indicator values and returns (is_bull, is_bear).
Works identically in backtest (called with numpy scalar values) and live (called with
float values from the market context dict).

No I/O, no side effects, no state.
"""

from __future__ import annotations


def check_ema_crossover(
    ema_f_cur: float,
    ema_f_prev: float,
    ema_s_cur: float,
    ema_s_prev: float,
    rsi: float,
    rsi_ob: float = 70.0,
    rsi_os: float = 30.0,
) -> tuple[bool, bool]:
    """EMA fast crosses slow EMA, confirmed by RSI not in opposite extreme."""
    is_bull = ema_f_cur > ema_s_cur and ema_f_prev <= ema_s_prev and rsi < rsi_ob
    is_bear = ema_f_cur < ema_s_cur and ema_f_prev >= ema_s_prev and rsi > rsi_os
    return is_bull, is_bear


def check_macd_crossover(
    hist_cur: float,
    hist_prev: float,
) -> tuple[bool, bool]:
    """MACD histogram crosses zero line."""
    is_bull = hist_cur > 0 and hist_prev <= 0
    is_bear = hist_cur < 0 and hist_prev >= 0
    return is_bull, is_bear


def check_rsi_reversal(
    rsi_cur: float,
    rsi_prev: float,
    rsi_ob: float = 70.0,
    rsi_os: float = 30.0,
) -> tuple[bool, bool]:
    """RSI crosses out of oversold (BUY) or overbought (SELL) zone."""
    is_bull = rsi_cur > rsi_os and rsi_prev <= rsi_os
    is_bear = rsi_cur < rsi_ob and rsi_prev >= rsi_ob
    return is_bull, is_bear


def check_bollinger(
    pct_b: float,
    buy_threshold: float = 0.2,
    sell_threshold: float = 0.8,
) -> tuple[bool, bool]:
    """
    Price near/below lower band (BUY) or near/above upper band (SELL).

    pct_b = (price - lower) / (upper - lower)
    0 = at lower band, 1 = at upper band
    """
    is_bull = pct_b <= buy_threshold
    is_bear = pct_b >= sell_threshold
    return is_bull, is_bear


def check_adx_trend(
    adx: float,
    plus_di: float,
    minus_di: float,
    threshold: float = 20.0,
) -> tuple[bool, bool]:
    """ADX above threshold and directional indicator confirms direction."""
    strong = adx > threshold
    is_bull = strong and plus_di > minus_di
    is_bear = strong and minus_di > plus_di
    return is_bull, is_bear


def check_bos(
    close: float,
    prev_swing_high: float | None,
    prev_swing_low: float | None,
) -> tuple[bool, bool]:
    """Break of Structure — close breaks above swing high (BUY) or below swing low (SELL)."""
    is_bull = prev_swing_high is not None and close > prev_swing_high
    is_bear = prev_swing_low is not None and close < prev_swing_low
    return is_bull, is_bear


def combine(
    results: list[tuple[bool, bool]],
    logic: str,
) -> tuple[bool, bool]:
    """
    Combine multiple (is_bull, is_bear) results using the given logic.

    logic:
        "AND"      — all must be bull/bear
        "OR"       — any must be bull/bear
        "MAJORITY" — >50% must be bull/bear
    """
    if not results:
        return False, False

    n = len(results)
    bull_count = sum(1 for b, _ in results if b)
    bear_count = sum(1 for _, b in results if b)

    if logic == "AND":
        return bull_count == n, bear_count == n
    elif logic == "OR":
        return bull_count > 0, bear_count > 0
    elif logic == "MAJORITY":
        return bull_count > n / 2, bear_count > n / 2
    else:
        # Unknown logic — fall back to AND
        return bull_count == n, bear_count == n
