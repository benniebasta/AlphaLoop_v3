"""
GET /api/live — Real-time live trading data for the Live Trading Monitor.

Provides price data, OHLC candles, session info, signal state, and volatility.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.instance import RunningInstance
from alphaloop.db.models.operational_event import OperationalEvent
from alphaloop.db.models.pipeline import PipelineDecision
from alphaloop.db.models.trade import TradeLog
from alphaloop.webui.deps import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/live", tags=["live"])

# ── yfinance timeframe map ───────────────────────────────────────────────────
_YF_TF = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}
_YF_PERIOD = {"1m": "5d", "5m": "5d", "15m": "5d", "30m": "5d", "1h": "30d", "4h": "60d", "1d": "90d", "1w": "2y"}

# Simple in-memory cache (symbol+tf → (timestamp, data), 30s TTL)
_cache: dict[str, tuple[float, tuple]] = {}
_CACHE_TTL = 30.0


async def _fetch_ohlc(symbol: str, timeframe: str) -> tuple:
    """Fetch OHLC candles from yfinance. Returns (ohlc_list, price, change_pct, day_high, day_low, vol_regime, atr_val, atr_pct)."""
    import asyncio
    import time

    cache_key = f"{symbol}:{timeframe}"
    now = time.monotonic()
    if cache_key in _cache and (now - _cache[cache_key][0]) < _CACHE_TTL:
        return _cache[cache_key][1]

    result = await asyncio.to_thread(_fetch_ohlc_sync, symbol, timeframe)
    _cache[cache_key] = (now, result)
    return result


def _fetch_ohlc_sync(symbol: str, timeframe: str) -> tuple:
    """Sync yfinance fetch — runs in thread pool."""
    import yfinance as yf

    # Map broker symbol to yfinance ticker
    try:
        from alphaloop.data.yf_catalog import get_yf_ticker
        ticker = get_yf_ticker(symbol) or symbol
    except ImportError:
        ticker = symbol

    yf_tf = _YF_TF.get(timeframe, "1h")
    yf_period = _YF_PERIOD.get(timeframe, "5d")

    df = yf.download(ticker, period=yf_period, interval=yf_tf, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return ([], None, None, None, None, "calm", None, None, None, "ranging", [], None)

    # Flatten multi-level columns if present
    if hasattr(df.columns, 'levels') and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    # Drop rows with NaN in OHLC columns — prevents invalid JSON (NaN literal)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    # Build OHLC array for LightweightCharts
    ohlc = []
    for ts, row in df.iterrows():
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        if any(x != x for x in (o, h, l, c)):  # NaN guard
            continue
        ohlc.append({
            "time": int(ts.timestamp()),
            "open": round(o, 5),
            "high": round(h, 5),
            "low": round(l, 5),
            "close": round(c, 5),
        })

    # Aggregate 1H → 4H if requested (yfinance has no native 4h interval)
    if timeframe == "4h" and ohlc:
        agg = []
        for i in range(0, len(ohlc), 4):
            chunk = ohlc[i:i+4]
            if not chunk:
                break
            agg.append({
                "time": chunk[0]["time"],
                "open": chunk[0]["open"],
                "high": max(b["high"] for b in chunk),
                "low": min(b["low"] for b in chunk),
                "close": chunk[-1]["close"],
            })
        ohlc = agg

    if not ohlc:
        return ([], None, None, None, None, "calm", None, None, None, "ranging", [], None)

    # Current price
    price = ohlc[-1]["close"]

    # Day high/low
    today_bars = [b for b in ohlc if b["time"] > ohlc[-1]["time"] - 86400]
    day_high = max(b["high"] for b in today_bars) if today_bars else ohlc[-1]["high"]
    day_low = min(b["low"] for b in today_bars) if today_bars else ohlc[-1]["low"]

    # Change %
    prev_close = ohlc[-2]["close"] if len(ohlc) > 1 else price
    change_pct = round(((price - prev_close) / prev_close) * 100, 3) if prev_close else 0

    # ATR for volatility regime
    closes = np.array([b["close"] for b in ohlc[-15:]])
    highs = np.array([b["high"] for b in ohlc[-15:]])
    lows = np.array([b["low"] for b in ohlc[-15:]])
    if len(closes) >= 2:
        tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]))
        atr_val = round(float(np.mean(tr)), 5)
        atr_pct = round((atr_val / price) * 100, 3) if price else None
        if atr_pct and atr_pct > 2.5:
            vol_regime = "extreme"
        elif atr_pct and atr_pct > 1.5:
            vol_regime = "elevated"
        elif atr_pct and atr_pct > 0.5:
            vol_regime = "normal"
        else:
            vol_regime = "calm"
    else:
        atr_val = None
        atr_pct = None
        vol_regime = "calm"

    # ── Signal Intelligence ─────────────────────────────────────────────────
    signal = None
    market_regime = "ranging"
    recent_signals: list[dict] = []

    closes_all = np.array([b["close"] for b in ohlc])
    if len(closes_all) >= 50:
        # EMA-9, EMA-21, EMA-50
        def ema(arr: np.ndarray, period: int) -> np.ndarray:
            k = 2.0 / (period + 1)
            out = np.empty_like(arr)
            out[0] = arr[0]
            for i in range(1, len(arr)):
                out[i] = arr[i] * k + out[i - 1] * (1 - k)
            return out

        ema9  = ema(closes_all, 9)
        ema21 = ema(closes_all, 21)
        ema50 = ema(closes_all, 50)

        # RSI-14
        def rsi(arr: np.ndarray, period: int = 14) -> float:
            deltas = np.diff(arr[-period - 1:])
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            avg_gain = gains.mean()
            avg_loss = losses.mean()
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100.0 - 100.0 / (1 + rs), 1)

        rsi_val = rsi(closes_all) if len(closes_all) > 15 else 50.0

        e9, e21, e50 = float(ema9[-1]), float(ema21[-1]), float(ema50[-1])
        prev_e9, prev_e21 = float(ema9[-2]), float(ema21[-2])

        # Market regime
        if e9 > e21 > e50:
            market_regime = "trending_up"
        elif e9 < e21 < e50:
            market_regime = "trending_down"
        else:
            market_regime = "ranging"

        # Signal: EMA crossover + RSI confirmation
        bullish_cross = prev_e9 <= prev_e21 and e9 > e21
        bearish_cross = prev_e9 >= prev_e21 and e9 < e21
        rsi_oversold  = rsi_val < 35
        rsi_overbought = rsi_val > 65

        direction = None
        confidence = 0.5
        if bullish_cross or (market_regime == "trending_up" and rsi_oversold):
            direction = "BUY"
            confidence = min(0.95, 0.55 + (35 - rsi_val) / 100 if rsi_oversold else 0.60 + (e9 - e21) / e21 * 10)
        elif bearish_cross or (market_regime == "trending_down" and rsi_overbought):
            direction = "SELL"
            confidence = min(0.95, 0.55 + (rsi_val - 65) / 100 if rsi_overbought else 0.60 + (e21 - e9) / e21 * 10)

        # Always expose current EMA state for the scanning panel
        ema_gap_pct = round((e9 - e21) / e21 * 100, 3) if e21 else 0.0
        ema_state = {
            "ema9": round(e9, 5),
            "ema21": round(e21, 5),
            "ema50": round(e50, 5),
            "rsi": rsi_val,
            "gap_pct": ema_gap_pct,
            "regime": market_regime,
        }

        if direction:
            signal = {
                "direction": direction,
                "confidence": round(max(0.0, min(1.0, confidence)), 2),
                "rsi": rsi_val,
                "ema9": round(e9, 5),
                "ema21": round(e21, 5),
                "source": "ema_cross" if (bullish_cross or bearish_cross) else "rsi_extreme",
                "timestamp": ohlc[-1]["time"],
            }

        # Scan last 50 bars for crossover history → recent signals
        scan_start = max(1, len(closes_all) - 50)
        for i in range(scan_start, len(closes_all)):
            if ema9[i - 1] <= ema21[i - 1] and ema9[i] > ema21[i]:
                recent_signals.append({"direction": "BUY",  "time": ohlc[i]["time"], "price": ohlc[i]["close"]})
            elif ema9[i - 1] >= ema21[i - 1] and ema9[i] < ema21[i]:
                recent_signals.append({"direction": "SELL", "time": ohlc[i]["time"], "price": ohlc[i]["close"]})
        recent_signals = recent_signals[-5:]  # keep last 5

    return (ohlc, price, change_pct, day_high, day_low, vol_regime, atr_val, atr_pct, signal, market_regime, recent_signals, ema_state if 'ema_state' in dir() else None)


# ── Session definitions (UTC) ────────────────────────────────────────────────

SESSIONS = [
    {"name": "Asia", "start": 0, "end": 6, "color": "#14b8a6"},
    {"name": "London", "start": 6, "end": 12, "color": "#22c55e"},
    {"name": "Overlap", "start": 12, "end": 15, "color": "#EF9F27"},
    {"name": "NY", "start": 15, "end": 20, "color": "#eab308"},
    {"name": "Off", "start": 20, "end": 24, "color": "#27272a"},
]


def _current_session() -> dict:
    """Determine the current trading session based on UTC hour."""
    now = datetime.now(timezone.utc)
    h = now.hour
    for s in SESSIONS:
        if s["start"] <= h < s["end"]:
            return {
                "name": s["name"],
                "closes_at": f"{s['end']:02d}:00 UTC",
                "is_open": s["name"] != "Off",
            }
    return {"name": "Off", "closes_at": "00:00 UTC", "is_open": False}


@router.get("")
async def live_data(
    symbol: str = Query(default="XAUUSD"),
    timeframe: str = Query(default="1m"),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Return real-time trading data for the Live Trading Monitor.

    This is a lightweight endpoint that aggregates data from multiple sources:
    - Price/OHLC: from MT5 connection or cached data
    - Session info: computed from UTC clock
    - Bot status: from RunningInstance table
    - Signal: from last WebSocket event (if available)
    """
    # Check if a bot is running for this symbol
    bot_q = select(RunningInstance).where(RunningInstance.symbol == symbol)
    bot_result = await session.execute(bot_q)
    running_bot = bot_result.scalars().first()

    # Get recent closed trades for this symbol
    recent_q = (
        select(TradeLog)
        .where(TradeLog.symbol == symbol)
        .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
        .order_by(TradeLog.closed_at.desc())
        .limit(5)
    )
    recent_trades = list((await session.execute(recent_q)).scalars())

    pipeline_q = (
        select(PipelineDecision)
        .where(PipelineDecision.symbol == symbol)
        .order_by(PipelineDecision.occurred_at.desc())
        .limit(5)
    )
    recent_pipeline = list((await session.execute(pipeline_q)).scalars())

    event_q = (
        select(OperationalEvent)
        .where(OperationalEvent.symbol == symbol)
        .order_by(OperationalEvent.created_at.desc())
        .limit(8)
    )
    recent_events = list((await session.execute(event_q)).scalars())

    current_sess = _current_session()

    # Phase 7G: Data source disclosure — this endpoint uses yfinance analytics,
    # NOT the actual bot's MT5 feed. Add source indicator for transparency.
    _data_source = "yfinance_analytics"  # not the live bot feed
    _bot_running = running_bot is not None
    _disclaimer = None if _bot_running else (
        "No bot running — data shown is from yfinance analytics, not live bot feed"
    )

    # Fetch OHLC data via yfinance (TODO: replace with bot event stream when available)
    ohlc = []
    price = None
    change_pct = None
    day_high = None
    day_low = None
    vol_regime = "calm"
    atr_value = None
    atr_pct = None
    computed_signal = None
    market_regime = "ranging"
    computed_recent_signals: list[dict] = []
    computed_ema_state = None

    try:
        ohlc, price, change_pct, day_high, day_low, vol_regime, atr_value, atr_pct, computed_signal, market_regime, computed_recent_signals, computed_ema_state = await _fetch_ohlc(
            symbol, timeframe
        )
    except Exception as e:
        logger.warning("Live data fetch failed for %s: %s", symbol, e)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "change_pct": change_pct,
        "day_high": day_high,
        "day_low": day_low,
        "ohlc": ohlc,
        "session": current_sess,
        "signal": computed_signal,
        "bot_running": _bot_running,
        "data_source": _data_source,
        "disclaimer": _disclaimer,
        "market_regime": market_regime,
        "recent_signals": computed_recent_signals,
        "volatility": {
            "regime": vol_regime,
            "atr_value": atr_value,
            "atr_pct": atr_pct,
        },
        "ema_state": computed_ema_state,
        "next_news": None,
        "agent_thoughts": [
            {
                "time": event.created_at.isoformat() if event.created_at else None,
                "severity": event.severity,
                "event_type": event.event_type,
                "message": event.message,
                "payload": event.payload,
            }
            for event in recent_events
        ],
        "pipeline_status": [
            {
                "time": decision.occurred_at.isoformat() if decision.occurred_at else None,
                "allowed": decision.allowed,
                "blocked_by": decision.blocked_by,
                "block_reason": decision.block_reason,
                "direction": decision.direction,
                "size_modifier": decision.size_modifier,
                "journey": (decision.tool_results or {}).get("journey"),
                "construction_source": (decision.tool_results or {}).get("construction_source"),
                "instance_id": decision.instance_id,
            }
            for decision in recent_pipeline
        ],
        "recent_trades": [
            {
                "direction": t.direction,
                "outcome": t.outcome,
                "pnl": t.pnl_usd,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            }
            for t in recent_trades
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/symbols")
async def live_symbols(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return list of tracked symbols with bot status."""
    bot_q = select(RunningInstance)
    bots = list((await session.execute(bot_q)).scalars())
    bot_symbols = {b.symbol for b in bots}

    # Default symbols to track
    symbols = ["XAUUSD", "BTCUSD", "EURUSD", "GBPUSD", "NAS100", "US30"]

    return {
        "symbols": [
            {
                "symbol": s,
                "bot_running": s in bot_symbols,
                "price": None,
                "change_pct": None,
            }
            for s in symbols
        ]
    }


@router.get("/sessions")
async def live_sessions() -> dict:
    """Return session timeline data."""
    now = datetime.now(timezone.utc)
    return {
        "sessions": SESSIONS,
        "current_time_utc": now.isoformat(),
        "current_hour": now.hour + now.minute / 60,
    }
