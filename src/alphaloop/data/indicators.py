"""
data/indicators.py
Pure technical indicator functions — pandas/numpy only, no external TA libs.

All functions are stateless and operate on pandas Series/DataFrame inputs.
Used by MarketContext builder and backtester.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Core indicators ──────────────────────────────────────────────────────────


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing via ewm)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi_values = 100 - (100 / (1 + rs))
    # When avg_loss is 0, RS is inf → RSI should be 100
    rsi_values = rsi_values.fillna(100.0)
    return rsi_values


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP — resets each calendar day.

    Requires columns: high, low, close, volume, time.
    Returns NaN where volume is zero.
    """
    times = pd.to_datetime(df["time"], utc=True)
    day = times.dt.date
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vol = df.groupby(day)["volume"].transform("cumsum")
    cum_tp_vol = (tp * df["volume"]).groupby(day).transform("cumsum")
    return cum_tp_vol / cum_vol.replace(0, np.nan)


# ── Structure / SMC indicators ───────────────────────────────────────────────


def detect_bos(
    df: pd.DataFrame,
    atr_val: float,
    lookback: int = 20,
    min_break_atr: float = 0.2,
) -> dict:
    """Detect Break of Structure using close-only confirmation.

    BUY  BOS: current close > max(prev lookback highs) by > min_break_atr x ATR
    SELL BOS: current close < min(prev lookback lows)  by > min_break_atr x ATR

    Returns dict with bullish_bos, bearish_bos, swing_high, swing_low, break distances.
    """
    if len(df) < lookback + 1 or atr_val <= 0:
        return {
            "bullish_bos": False,
            "bearish_bos": False,
            "swing_high": None,
            "swing_low": None,
            "bullish_break_atr": 0.0,
            "bearish_break_atr": 0.0,
        }
    min_break = min_break_atr * atr_val
    current_close = float(df["close"].iloc[-1])
    prev = df.iloc[-(lookback + 1) : -1]
    swing_high = float(prev["high"].max())
    swing_low = float(prev["low"].min())
    bullish_dist = current_close - swing_high
    bearish_dist = swing_low - current_close
    return {
        "bullish_bos": bullish_dist > min_break,
        "bearish_bos": bearish_dist > min_break,
        "swing_high": round(swing_high, 5),
        "swing_low": round(swing_low, 5),
        "bullish_break_atr": round(bullish_dist / atr_val, 3),
        "bearish_break_atr": round(bearish_dist / atr_val, 3),
    }


def detect_fvg(
    df: pd.DataFrame,
    atr_val: float,
    lookback: int = 20,
    min_size_atr: float = 0.05,
) -> dict:
    """Detect Fair Value Gaps (3-candle price imbalances).

    Bullish FVG: df[i-2].high < df[i].low  (upside gap)
    Bearish FVG: df[i-2].low  > df[i].high (downside gap)

    Only unfilled gaps are returned (up to 3 most recent each side).
    """
    bullish_fvgs: list[dict] = []
    bearish_fvgs: list[dict] = []
    min_size = atr_val * min_size_atr if atr_val > 0 else 0.0
    n = len(df)
    if n < 3:
        return {"bullish": [], "bearish": [], "has_bullish": False, "has_bearish": False}

    current_price = float(df["close"].iloc[-1])
    start = max(2, n - lookback)

    for i in range(start, n):
        h_prev2 = float(df["high"].iloc[i - 2])
        l_prev2 = float(df["low"].iloc[i - 2])
        h_cur = float(df["high"].iloc[i])
        l_cur = float(df["low"].iloc[i])

        # Bullish FVG
        if l_cur > h_prev2 and (l_cur - h_prev2) >= min_size:
            if current_price >= h_prev2:
                gap_sz = round((l_cur - h_prev2) / atr_val, 2) if atr_val > 0 else 0
                mid = round((h_prev2 + l_cur) / 2, 5)
                bullish_fvgs.append({
                    "top": round(l_cur, 5),
                    "bottom": round(h_prev2, 5),
                    "midpoint": mid,
                    "size_atr": gap_sz,
                })

        # Bearish FVG
        if h_cur < l_prev2 and (l_prev2 - h_cur) >= min_size:
            if current_price <= l_prev2:
                gap_sz = round((l_prev2 - h_cur) / atr_val, 2) if atr_val > 0 else 0
                mid = round((h_cur + l_prev2) / 2, 5)
                bearish_fvgs.append({
                    "top": round(l_prev2, 5),
                    "bottom": round(h_cur, 5),
                    "midpoint": mid,
                    "size_atr": gap_sz,
                })

    return {
        "bullish": bullish_fvgs[-3:],
        "bearish": bearish_fvgs[-3:],
        "has_bullish": len(bullish_fvgs) > 0,
        "has_bearish": len(bearish_fvgs) > 0,
    }


def find_swing_highs_lows(df: pd.DataFrame, lookback: int = 5) -> dict:
    """Identify swing highs/lows and market structure (HH/HL/LH/LL)."""
    highs: list[dict] = []
    lows: list[dict] = []
    high_vals = df["high"].values
    low_vals = df["low"].values

    for i in range(lookback, len(df) - lookback):
        window_h = high_vals[i - lookback : i + lookback + 1]
        window_l = low_vals[i - lookback : i + lookback + 1]
        if high_vals[i] == window_h.max():
            highs.append({"index": i, "price": float(high_vals[i])})
        if low_vals[i] == window_l.min():
            lows.append({"index": i, "price": float(low_vals[i])})

    structure = "ranging"
    if len(highs) >= 2 and len(lows) >= 2:
        last_2_highs = [h["price"] for h in highs[-2:]]
        last_2_lows = [lo["price"] for lo in lows[-2:]]
        if last_2_highs[1] > last_2_highs[0] and last_2_lows[1] > last_2_lows[0]:
            structure = "bullish"
        elif last_2_highs[1] < last_2_highs[0] and last_2_lows[1] < last_2_lows[0]:
            structure = "bearish"

    return {
        "swing_highs": highs[-5:],
        "swing_lows": lows[-5:],
        "structure": structure,
        "last_significant_high": highs[-1]["price"] if highs else None,
        "last_significant_low": lows[-1]["price"] if lows else None,
    }


# ── Momentum / Volatility indicators ────────────────────────────────────────


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict:
    """MACD — Moving Average Convergence Divergence.

    Returns dict with macd_line, signal_line, histogram (all pd.Series),
    plus scalar last_histogram and crossover booleans.
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    last_hist = float(histogram.iloc[-1]) if len(histogram) > 0 else 0.0
    prev_hist = float(histogram.iloc[-2]) if len(histogram) > 1 else 0.0
    return {
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
        "last_histogram": round(last_hist, 5),
        "bullish_cross": prev_hist <= 0 < last_hist,
        "bearish_cross": prev_hist >= 0 > last_hist,
        "histogram_positive": last_hist > 0,
    }


def bollinger(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict:
    """Bollinger Bands — middle band + upper/lower envelopes.

    Returns dict with middle, upper, lower (pd.Series),
    plus scalar band_width and pct_b (position within bands, 0-1).
    """
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    last_price = float(series.iloc[-1]) if len(series) > 0 else 0.0
    last_upper = float(upper.iloc[-1]) if len(upper) > 0 else 0.0
    last_lower = float(lower.iloc[-1]) if len(lower) > 0 else 0.0
    band_range = last_upper - last_lower
    pct_b = (last_price - last_lower) / band_range if band_range > 0 else 0.5
    return {
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "last_upper": round(last_upper, 5),
        "last_lower": round(last_lower, 5),
        "band_width": round(band_range, 5),
        "pct_b": round(pct_b, 3),
        "near_lower": pct_b < 0.2,
        "near_upper": pct_b > 0.8,
    }


def adx(df: pd.DataFrame, period: int = 14) -> dict:
    """Average Directional Index — trend strength indicator.

    ADX > 25 = trending, ADX < 20 = ranging/weak.
    Returns dict with adx value, plus_di, minus_di, and trending flag.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)
    # Only keep the larger directional movement
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    smooth_tr = tr.ewm(com=period - 1, adjust=False).mean()
    smooth_plus = plus_dm.ewm(com=period - 1, adjust=False).mean()
    smooth_minus = minus_dm.ewm(com=period - 1, adjust=False).mean()

    plus_di = 100 * smooth_plus / smooth_tr.replace(0, np.nan)
    minus_di = 100 * smooth_minus / smooth_tr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(com=period - 1, adjust=False).mean()

    last_adx = float(adx_val.iloc[-1]) if len(adx_val) > 0 else 0.0
    last_plus = float(plus_di.iloc[-1]) if len(plus_di) > 0 else 0.0
    last_minus = float(minus_di.iloc[-1]) if len(minus_di) > 0 else 0.0
    return {
        "adx": round(last_adx, 2),
        "plus_di": round(last_plus, 2),
        "minus_di": round(last_minus, 2),
        "trending": last_adx >= 25,
        "strong_trend": last_adx >= 40,
        "no_trend": last_adx < 20,
    }


def volume_ma(volume: pd.Series, period: int = 20) -> dict:
    """Volume Moving Average — simple volume confirmation.

    Returns dict with average, ratio (current / avg), and above_average flag.
    """
    avg = volume.rolling(window=period).mean()
    last_vol = float(volume.iloc[-1]) if len(volume) > 0 else 0.0
    last_avg = float(avg.iloc[-1]) if len(avg) > 0 else 0.0
    ratio = last_vol / last_avg if last_avg > 0 else 1.0
    return {
        "average": round(last_avg, 2),
        "current": round(last_vol, 2),
        "ratio": round(ratio, 3),
        "above_average": ratio >= 1.0,
        "strong_volume": ratio >= 1.5,
        "weak_volume": ratio < 0.8,
    }
