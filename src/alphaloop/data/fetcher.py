"""
data/fetcher.py
Async OHLCV data ingestion.

Primary: MetaTrader 5 via asyncio.to_thread (MT5 API is synchronous).
Fallback: yfinance for testing / paper trading.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
import numpy as np
import pandas as pd

from alphaloop.core.errors import AlphaLoopError

logger = logging.getLogger(__name__)

# MT5-compatible timeframe constants
TIMEFRAMES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

# Map broker symbols to yfinance tickers — uses full catalog with legacy fallback
try:
    from alphaloop.data.yf_catalog import SYMBOL_TO_YF as _YF_TICKER_MAP
except ImportError:
    _YF_TICKER_MAP: dict[str, str] = {
        "XAUUSD": "GC=F",
        "BTCUSD": "BTC-USD",
        "ETHUSD": "ETH-USD",
        "EURUSD": "EURUSD=X",
        "GBPUSD": "GBPUSD=X",
        "USDJPY": "JPY=X",
        "NAS100": "NQ=F",
        "US30": "YM=F",
        "US500": "ES=F",
    }


class DataFetchError(AlphaLoopError):
    """OHLCV data fetch or staleness failure."""


class OHLCVFetcher:
    """
    Async OHLCV data fetcher with caching and staleness checks.

    All public methods are async. MT5 calls are dispatched via
    asyncio.to_thread to avoid blocking the event loop.
    """

    def __init__(
        self,
        symbol: str = "XAUUSD",
        use_mt5: bool = True,
        mt5_server: str | None = None,
        mt5_login: int | None = None,
        mt5_password: str | None = None,
    ) -> None:
        self.symbol = symbol
        self.use_mt5 = use_mt5
        self._mt5_server = mt5_server
        self._mt5_login = mt5_login
        self._mt5_password = mt5_password
        self._mt5_initialized = False
        self._cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
        self._cache_ttl: dict[str, int] = {"M1": 60, "M5": 290, "M15": 290, "H1": 300}

    # ── MT5 init (sync, run in thread) ────────────────────────────────────────

    def _init_mt5_sync(self) -> None:
        """Initialize MT5 connection — called inside thread."""
        import MetaTrader5 as mt5

        kwargs = {}
        if self._mt5_server:
            kwargs["server"] = self._mt5_server
        if self._mt5_login:
            kwargs["login"] = self._mt5_login
        if self._mt5_password:
            kwargs["password"] = self._mt5_password

        if not mt5.initialize(**kwargs):
            raise DataFetchError(f"MT5 init failed: {mt5.last_error()}")
        self._mt5_initialized = True
        logger.info("MT5 connected successfully")

    async def _ensure_mt5(self) -> None:
        if not self._mt5_initialized and self.use_mt5:
            try:
                await asyncio.to_thread(self._init_mt5_sync)
            except ImportError:
                logger.warning("MetaTrader5 not installed — falling back to yfinance")
                self.use_mt5 = False

    # ── Public async API ──────────────────────────────────────────────────────

    async def get_ohlcv(self, timeframe: str = "M15", bars: int = 200) -> pd.DataFrame:
        """Fetch OHLCV bars with caching and staleness check."""
        now = datetime.now(timezone.utc)
        cache_key = f"{timeframe}_{bars}"
        cached = self._cache.get(cache_key)
        ttl = self._cache_ttl.get(timeframe, 120)

        if cached and (now - cached[0]).total_seconds() < ttl:
            return cached[1]

        await self._ensure_mt5()

        if self.use_mt5:
            df = await asyncio.to_thread(self._fetch_mt5_sync, timeframe, bars)
        else:
            df = await asyncio.to_thread(self._fetch_yfinance_sync, timeframe, bars)

        # Staleness check
        if not df.empty and "time" in df.columns:
            last_bar_time = pd.Timestamp(df["time"].iloc[-1], tz="UTC")
            tf_minutes = TIMEFRAMES.get(timeframe, 15)
            staleness_limit = tf_minutes * 2
            age_minutes = (now - last_bar_time).total_seconds() / 60
            if age_minutes > staleness_limit:
                raise DataFetchError(
                    f"Stale OHLCV for {self.symbol} {timeframe}: "
                    f"last bar {last_bar_time.isoformat()} is {age_minutes:.0f}min old "
                    f"(limit: {staleness_limit}min)"
                )

        self._cache[cache_key] = (now, df)
        return df

    async def get_current_price(self) -> dict:
        """Returns bid/ask/spread dict."""
        await self._ensure_mt5()
        if self.use_mt5:
            return await asyncio.to_thread(self._get_price_mt5_sync)
        df = await self.get_ohlcv("M1", 200)
        price = float(df["close"].iloc[-1])
        return {
            "bid": price - 0.20,
            "ask": price + 0.20,
            "spread": 0.40,
            "time": datetime.now(timezone.utc),
        }

    async def get_multi_timeframe(self, bars: int = 201) -> dict[str, pd.DataFrame]:
        """Fetch M5, M15, H1 concurrently."""
        results = await asyncio.gather(
            self.get_ohlcv("M5", bars),
            self.get_ohlcv("M15", bars),
            self.get_ohlcv("H1", bars),
        )
        return {"M5": results[0], "M15": results[1], "H1": results[2]}

    # ── Sync internals (run inside to_thread) ─────────────────────────────────

    def _fetch_mt5_sync(self, timeframe: str, bars: int) -> pd.DataFrame:
        import MetaTrader5 as mt5

        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        rates = mt5.copy_rates_from_pos(self.symbol, tf_map[timeframe], 0, bars)
        if rates is None:
            raise DataFetchError(f"MT5 copy_rates failed: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df[["time", "open", "high", "low", "close", "tick_volume"]].rename(
            columns={"tick_volume": "volume"}
        )

    def _fetch_yfinance_sync(self, timeframe: str, bars: int) -> pd.DataFrame:
        import yfinance as yf

        tf_map = {"M1": "1m", "M5": "5m", "M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}
        period_map = {"M1": "7d", "M5": "60d", "M15": "60d", "H1": "2y", "H4": "2y", "D1": "5y"}

        sym = self.symbol.rstrip("mM").upper()
        ticker_str = _YF_TICKER_MAP.get(sym)
        if ticker_str is None:
            raise DataFetchError(
                f"Unknown symbol '{sym}' — no yfinance mapping. "
                f"Known: {list(_YF_TICKER_MAP.keys())}"
            )

        ticker = yf.Ticker(ticker_str)
        df = ticker.history(period=period_map[timeframe], interval=tf_map[timeframe])
        df = df.reset_index()
        df = df.rename(columns={
            "Datetime": "time", "Date": "time",
            "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        })
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df[["time", "open", "high", "low", "close", "volume"]].tail(bars)

    def _get_price_mt5_sync(self) -> dict:
        import MetaTrader5 as mt5

        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise DataFetchError("Cannot get tick data from MT5")
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": round(tick.ask - tick.bid, 2),
            "time": datetime.fromtimestamp(tick.time, tz=timezone.utc),
        }
