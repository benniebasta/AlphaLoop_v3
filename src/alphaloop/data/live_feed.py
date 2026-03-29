"""
Live Data Feed — Real-time price streaming.

Provides periodic price updates for the Live Trading Monitor.
Uses MT5 tick data when available, falls back to yfinance polling.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TickData:
    symbol: str
    bid: float
    ask: float
    spread: float
    timestamp: float = field(default_factory=time.time)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class LiveFeed:
    """
    Provides real-time price data for one or more symbols.

    Caches the last tick per symbol and updates periodically.
    The Live page polls this instead of hitting yfinance directly.
    """

    def __init__(self, poll_interval: float = 5.0) -> None:
        self._poll_interval = poll_interval
        self._ticks: dict[str, TickData] = {}
        self._running = False
        self._symbols: set[str] = set()

    def subscribe(self, symbol: str) -> None:
        self._symbols.add(symbol)

    def unsubscribe(self, symbol: str) -> None:
        self._symbols.discard(symbol)

    def get_tick(self, symbol: str) -> TickData | None:
        return self._ticks.get(symbol)

    def get_all_ticks(self) -> dict[str, dict]:
        return {
            sym: {
                "bid": t.bid,
                "ask": t.ask,
                "spread": round(t.spread, 5),
                "mid": round(t.mid, 5),
                "timestamp": t.timestamp,
            }
            for sym, t in self._ticks.items()
        }

    async def start(self) -> None:
        """Start the polling loop."""
        self._running = True
        logger.info("LiveFeed started for %d symbols", len(self._symbols))
        while self._running:
            await self._poll_all()
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    async def _poll_all(self) -> None:
        """Poll all subscribed symbols."""
        for symbol in list(self._symbols):
            try:
                tick = await self._fetch_tick(symbol)
                if tick:
                    self._ticks[symbol] = tick
            except Exception as e:
                logger.debug("LiveFeed poll failed for %s: %s", symbol, e)

    async def _fetch_tick(self, symbol: str) -> TickData | None:
        """Fetch current tick for a symbol. MT5 primary, yfinance fallback."""
        # Try MT5 first
        tick = await self._fetch_mt5(symbol)
        if tick:
            return tick
        # Fallback to yfinance
        return await self._fetch_yfinance(symbol)

    async def _fetch_mt5(self, symbol: str) -> TickData | None:
        """Try to get tick from MT5."""
        try:
            import MetaTrader5 as mt5
            tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
            if tick:
                return TickData(
                    symbol=symbol,
                    bid=tick.bid,
                    ask=tick.ask,
                    spread=tick.ask - tick.bid,
                )
        except (ImportError, Exception):
            pass
        return None

    async def _fetch_yfinance(self, symbol: str) -> TickData | None:
        """Fallback: get latest price from yfinance."""
        def _fetch():
            import yfinance as yf
            try:
                from alphaloop.data.yf_catalog import get_yf_ticker
                ticker = get_yf_ticker(symbol) or symbol
            except ImportError:
                ticker = symbol
            data = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
            if data is not None and not data.empty:
                if hasattr(data.columns, 'levels') and data.columns.nlevels > 1:
                    data.columns = data.columns.get_level_values(0)
                last = data.iloc[-1]
                close = float(last["Close"])
                # Simulate spread (yfinance doesn't provide bid/ask)
                spread = close * 0.0001  # 1 pip estimate
                return TickData(
                    symbol=symbol,
                    bid=close - spread / 2,
                    ask=close + spread / 2,
                    spread=spread,
                )
            return None

        return await asyncio.to_thread(_fetch)
