"""
data/market_context.py
MarketContext Pydantic model + async builder.

MarketContext is the single data object passed to every tool in the pipeline.
It aggregates OHLCV data, indicators, news, session info, DXY, and sentiment
into one immutable snapshot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from alphaloop.core.types import SessionName
from alphaloop.data.fetcher import OHLCVFetcher
from alphaloop.data.indicators import atr, ema, rsi, vwap, detect_bos, detect_fvg

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

    return {
        "ema21": round(float(ema21.iloc[-1]), 2) if len(ema21) > 0 else None,
        "ema55": round(float(ema55.iloc[-1]), 2) if len(ema55) > 0 else None,
        "ema200": round(ema200_last, 2) if ema200_last else None,
        "rsi": round(float(rsi14.iloc[-1]), 2),
        "atr": round(atr_val, 2),
        "atr_pct": round(atr_val / price_now * 100, 4) if price_now > 0 else 0.0,
        "trend_bias": trend_bias,
        "vwap": vwap_val,
        "bos": bos_data,
        "fvg": fvg_data,
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
    import asyncio

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
    )
