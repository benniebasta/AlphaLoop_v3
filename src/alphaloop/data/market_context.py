"""
data/market_context.py
MarketContext Pydantic model + async builder.

MarketContext is the single data object passed to every tool in the pipeline.
It aggregates OHLCV data, indicators, news, session info, DXY, and sentiment
into one immutable snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from alphaloop.core.types import SessionName
from alphaloop.data.fetcher import OHLCVFetcher
from alphaloop.data.indicators import (
    atr, ema, rsi, vwap, detect_bos, detect_fvg,
    macd, bollinger, adx, volume_ma, find_swing_highs_lows,
    alma, trendilo, choppiness_index, fast_fingers,
)

logger = logging.getLogger(__name__)


class SessionInfo(BaseModel):
    """Current trading session metadata."""

    name: str = "unknown"
    score: float = 0.0
    hour_utc: int = 0
    is_weekend: bool = False


class PriceSnapshot(BaseModel):
    """Current bid/ask/spread."""

    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MarketContext(BaseModel):
    """
    Complete market snapshot passed to every pipeline tool.

    Carries OHLCV DataFrames, computed indicators, news events,
    session info, DXY data, and sentiment — everything a tool needs
    to make a pass/block decision.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Identity
    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    trade_direction: str = "BUY"

    # Price data
    price: PriceSnapshot = Field(default_factory=PriceSnapshot)
    df: Optional[pd.DataFrame] = None  # Primary timeframe OHLCV

    # Multi-timeframe DataFrames
    timeframes: dict[str, pd.DataFrame] = Field(default_factory=dict)

    # Pre-computed indicators per timeframe
    indicators: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # News calendar events
    news: list[dict[str, Any]] = Field(default_factory=list)

    # Session info
    session: SessionInfo = Field(default_factory=SessionInfo)

    # DXY data
    dxy: dict[str, Any] = Field(default_factory=dict)

    # Sentiment data
    sentiment: dict[str, Any] = Field(default_factory=dict)

    # Open trades (for correlation guard)
    open_trades: dict[str, Any] = Field(default_factory=dict)

    # Risk monitor reference (opaque — tools call can_open_trade())
    risk_monitor: Any = None

    # Extra context for tools
    extra: dict[str, Any] = Field(default_factory=dict)

    # Regime classification (computed from indicators in build_market_context)
    regime: str = "neutral"          # trending | ranging | volatile | dead | neutral
    macro_regime: str = "neutral"    # risk_on | risk_off | neutral


def _compute_indicators_for_df(df: pd.DataFrame) -> dict[str, Any]:
    """Compute standard indicator dict from an OHLCV DataFrame."""
    if len(df) < 30:
        return {}

    close = df["close"]
    ema21 = ema(close, 21)
    ema55 = ema(close, 55)
    ema200 = ema(close, 200)
    rsi14 = rsi(close, 14)
    atr14 = atr(df, 14)

    atr_val = float(atr14.iloc[-1])
    price_now = float(close.iloc[-1])

    ema200_last = float(ema200.iloc[-1]) if len(ema200) > 0 else None
    if ema200_last is not None and pd.isna(ema200_last):
        ema200_last = None

    trend_bias = None
    if ema200_last is not None:
        trend_bias = "bullish" if price_now > ema200_last else "bearish"

    # VWAP
    vwap_val = None
    if "volume" in df.columns and df["volume"].sum() > 0:
        try:
            vwap_series = vwap(df)
            vwap_val = round(float(vwap_series.iloc[-1]), 2) if not vwap_series.empty else None
        except Exception:
            pass

    # Structure
    bos_data = detect_bos(df, atr_val, lookback=20, min_break_atr=0.2)
    fvg_data = detect_fvg(df, atr_val, lookback=20, min_size_atr=0.05)

    # MACD
    macd_data = macd(close)

    # Bollinger Bands
    bb_data = bollinger(close)

    # ADX
    adx_data = adx(df)

    # Volume
    vol_data = {}
    if "volume" in df.columns and df["volume"].sum() > 0:
        vol_data = volume_ma(df["volume"])

    # Swing structure
    swing_data = find_swing_highs_lows(df)

    # Tick jump (2-bar move relative to ATR)
    tick_jump_atr = None
    if len(close) >= 3 and atr_val > 0:
        tick_jump_atr = round(abs(float(close.iloc[-1]) - float(close.iloc[-3])) / atr_val, 3)

    # Liquidity vacuum (last bar body vs range)
    liq_data = None
    if len(df) >= 1:
        last_bar = df.iloc[-1]
        bar_range = float(last_bar["high"] - last_bar["low"])
        bar_body = abs(float(last_bar["close"] - last_bar["open"]))
        if bar_range > 0 and atr_val > 0:
            liq_data = {
                "bar_range_atr": round(bar_range / atr_val, 3),
                "body_pct": round(bar_body / bar_range * 100, 1),
            }

    # ALMA
    alma_val = None
    try:
        alma_series = alma(close)
        if len(alma_series) > 0 and not pd.isna(alma_series.iloc[-1]):
            alma_val = round(float(alma_series.iloc[-1]), 5)
    except Exception:
        pass

    # Trendilo
    trendilo_data = trendilo(close, atr_series=atr14)

    # Choppiness Index
    chop_data = choppiness_index(df)

    # Fast Fingers
    ff_data = fast_fingers(close)

    return {
        "ema21": round(float(ema21.iloc[-1]), 2) if len(ema21) > 0 else None,
        "ema55": round(float(ema55.iloc[-1]), 2) if len(ema55) > 0 else None,
        "ema200": round(ema200_last, 2) if ema200_last else None,
        "ema_fast": round(float(ema21.iloc[-1]), 2) if len(ema21) > 0 else None,
        "ema_slow": round(float(ema55.iloc[-1]), 2) if len(ema55) > 0 else None,
        "rsi": round(float(rsi14.iloc[-1]), 2),
        "atr": round(atr_val, 2),
        "atr_pct": round(atr_val / price_now * 100, 4) if price_now > 0 else 0.0,
        "trend_bias": trend_bias,
        "vwap": vwap_val,
        "bos": bos_data,
        "fvg": fvg_data,
        # MACD
        "macd_histogram": macd_data["last_histogram"],
        "macd_bullish_cross": macd_data["bullish_cross"],
        "macd_bearish_cross": macd_data["bearish_cross"],
        # Bollinger Bands
        "bb_pct_b": bb_data["pct_b"],
        "bb_upper": bb_data["last_upper"],
        "bb_lower": bb_data["last_lower"],
        "bb_band_width": bb_data["band_width"],
        # ADX
        "adx": adx_data["adx"],
        "adx_plus_di": adx_data["plus_di"],
        "adx_minus_di": adx_data["minus_di"],
        # Volume
        "volume_ratio": vol_data.get("ratio"),
        # Swing structure
        "swing_structure": swing_data["structure"],
        "swing_highs": swing_data["swing_highs"],
        "swing_lows": swing_data["swing_lows"],
        "last_significant_high": swing_data.get("last_significant_high"),
        "last_significant_low": swing_data.get("last_significant_low"),
        # Tick jump
        "tick_jump_atr": tick_jump_atr,
        # Liquidity vacuum
        "liq_vacuum": liq_data,
        # ALMA
        "alma": alma_val,
        # Trendilo
        "trendilo": trendilo_data,
        # Choppiness Index
        "choppiness": chop_data,
        # Fast Fingers
        "fast_fingers": ff_data,
    }


def _get_session_info() -> SessionInfo:
    """Determine current trading session from UTC hour."""
    now = datetime.now(timezone.utc)
    hour = now.hour
    weekday = now.weekday()

    if weekday >= 5:
        return SessionInfo(name="weekend", score=0.0, hour_utc=hour, is_weekend=True)
    if 13 <= hour < 16:
        return SessionInfo(name=SessionName.LONDON_NY_OVERLAP, score=1.0, hour_utc=hour)
    if 13 <= hour < 21:
        return SessionInfo(name=SessionName.NY, score=0.85, hour_utc=hour)
    if 7 <= hour < 16:
        return SessionInfo(name=SessionName.LONDON, score=0.80, hour_utc=hour)
    if 4 <= hour < 7:
        return SessionInfo(name=SessionName.ASIA_LATE, score=0.40, hour_utc=hour)
    return SessionInfo(name=SessionName.ASIA_EARLY, score=0.20, hour_utc=hour)


def _classify_regime(indicators: dict) -> str:
    """
    Classify market regime from indicator dict (H1 preferred, M15 fallback).

    Returns one of: trending | ranging | volatile | dead | neutral
    Uses choppiness CI (trending < 38.2, ranging > 61.8), ADX, and ATR%.
    """
    chop_raw = indicators.get("choppiness")
    if isinstance(chop_raw, dict):
        chop_val = float(chop_raw.get("ci", 50) or 50)
    elif chop_raw is not None:
        try:
            chop_val = float(chop_raw)
        except (TypeError, ValueError):
            chop_val = 50.0
    else:
        chop_val = 50.0

    atr_pct = float(indicators.get("atr_pct") or 0)
    adx_raw = indicators.get("adx")
    try:
        adx_val = float(adx_raw) if adx_raw is not None else 20.0
    except (TypeError, ValueError):
        adx_val = 20.0

    if atr_pct < 0.15:
        return "dead"
    if chop_val > 61.8 or adx_val < 15:
        return "ranging"
    if chop_val < 38.2 and adx_val > 25:
        return "trending"
    if atr_pct > 0.7:
        return "volatile"
    return "neutral"


def _enrich_dxy(dxy: dict) -> dict:
    """
    Enrich raw DXY data with human-readable strength label and trend direction.

    Adds:
      strength_label: "strong" | "moderate" | "weak"
      trend:          "rising" | "declining" | "flat"

    These are derived from the existing numeric fields already returned by
    fetch_dxy_bias() (strength float 0–1, change_1d_pct).
    """
    raw = dict(dxy)  # copy — don't mutate caller's dict

    strength_val = float(raw.get("strength", 0.0) or 0.0)
    if strength_val >= 0.6:
        raw["strength_label"] = "strong"
    elif strength_val >= 0.3:
        raw["strength_label"] = "moderate"
    else:
        raw["strength_label"] = "weak"

    chg_1d = float(raw.get("change_1d_pct", 0.0) or 0.0)
    if chg_1d > 0.15:
        raw["trend"] = "rising"
    elif chg_1d < -0.15:
        raw["trend"] = "declining"
    else:
        raw["trend"] = "flat"

    # level alias for cleaner prompt formatting
    if "current_level" in raw and raw["current_level"] is not None:
        raw["level"] = raw["current_level"]

    return raw


def _classify_macro_regime(dxy: dict, sentiment: dict) -> str:
    """
    Derive macro risk regime from DXY bias and sentiment data.

    Returns one of: risk_on | risk_off | neutral
    - risk_off: DXY bullish + strong → flight to safety
    - risk_on:  DXY bearish + bullish sentiment → risk appetite
    - neutral:  all other combinations

    Uses enriched DXY fields (strength_label, trend) for higher precision:
    strong rising DXY = clear risk_off; weak/flat DXY = insufficient signal.
    """
    dxy_bias = str(dxy.get("bias", "") or "").lower()
    dxy_strength = str(dxy.get("strength_label", "weak") or "weak").lower()
    dxy_trend = str(dxy.get("trend", "flat") or "flat").lower()
    sent_bias = str(sentiment.get("bias", "") or "").lower()

    # Strong risk_off: DXY bullish AND (strong strength OR rising trend)
    if "bull" in dxy_bias and ("bull" not in sent_bias):
        if dxy_strength in ("strong", "moderate") or dxy_trend == "rising":
            return "risk_off"

    # risk_on: DXY bearish + bullish sentiment
    if "bear" in dxy_bias and "bull" in sent_bias:
        return "risk_on"

    return "neutral"


async def build_market_context(
    fetcher: OHLCVFetcher,
    trade_direction: str = "BUY",
    news_fetcher=None,
    dxy_fetcher=None,
    sentiment_fetcher=None,
) -> MarketContext:
    """
    Async builder — assembles a complete MarketContext snapshot.

    Fetches OHLCV data, computes indicators, and optionally fetches
    news, DXY, and sentiment data. Failures in auxiliary data sources
    are caught and defaulted (never block the context build).
    """
    # Fetch multi-timeframe OHLCV
    multi_tf = await fetcher.get_multi_timeframe(bars=201)
    tick = await fetcher.get_current_price()

    # Compute indicators on closed bars (exclude forming bar)
    indicators: dict[str, dict] = {}
    for tf, df in multi_tf.items():
        closed = df.iloc[:-1] if len(df) > 1 else df
        indicators[tf] = _compute_indicators_for_df(closed)

    # Session info
    session = _get_session_info()

    # Auxiliary data — fetch concurrently, catch failures
    news_events: list[dict] = []
    dxy_data: dict = {}
    sentiment_data: dict = {}

    async def _fetch_news():
        nonlocal news_events
        if news_fetcher:
            try:
                news_events = await news_fetcher()
            except Exception as e:
                logger.warning(f"News fetch failed: {e}")

    async def _fetch_dxy():
        nonlocal dxy_data
        if dxy_fetcher:
            try:
                dxy_data = await dxy_fetcher()
            except Exception as e:
                logger.warning(f"DXY fetch failed: {e}")

    async def _fetch_sentiment():
        nonlocal sentiment_data
        if sentiment_fetcher:
            try:
                sentiment_data = await sentiment_fetcher()
            except Exception as e:
                logger.warning(f"Sentiment fetch failed: {e}")

    await asyncio.gather(_fetch_news(), _fetch_dxy(), _fetch_sentiment())

    # Enrich DXY with strength_label, trend, level fields
    dxy_data = _enrich_dxy(dxy_data)

    # Regime classification — use H1 (higher-TF perspective), fall back to M15
    _regime_ind = indicators.get("H1") or indicators.get("M15") or {}
    regime = _classify_regime(_regime_ind)
    macro_regime = _classify_macro_regime(dxy_data, sentiment_data)

    # Primary timeframe
    primary_tf = "M15"
    primary_df = multi_tf.get(primary_tf, pd.DataFrame())

    return MarketContext(
        symbol=fetcher.symbol,
        timeframe=primary_tf,
        trade_direction=trade_direction.upper(),
        price=PriceSnapshot(
            bid=tick["bid"],
            ask=tick["ask"],
            spread=tick["spread"],
            time=tick["time"],
        ),
        df=primary_df,
        timeframes=multi_tf,
        indicators=indicators,
        news=news_events,
        session=session,
        dxy=dxy_data,
        sentiment=sentiment_data,
        regime=regime,
        macro_regime=macro_regime,
    )
