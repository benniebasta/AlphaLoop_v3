"""Service boundary for validated market data access."""

from __future__ import annotations

from alphaloop.data.fetcher import OHLCVFetcher


class MarketDataService:
    """Thin wrapper around the validated fetcher interface."""

    def __init__(self, fetcher: OHLCVFetcher | None = None, *, symbol: str = "XAUUSD") -> None:
        self._fetcher = fetcher or OHLCVFetcher(symbol=symbol)

    async def get_ohlcv(self, timeframe: str = "M15", bars: int = 200):
        return await self._fetcher.get_ohlcv(timeframe=timeframe, bars=bars)

    async def get_current_price(self) -> dict:
        return await self._fetcher.get_current_price()

    async def get_multi_timeframe(self, bars: int = 201) -> dict:
        return await self._fetcher.get_multi_timeframe(bars=bars)
