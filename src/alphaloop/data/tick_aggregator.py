"""
data/tick_aggregator.py — High-frequency MT5 tick polling via thread bridge.

Replaces the LiveFeed's 5s sleep loop with a 100ms thread-based tick
poller that pushes updates into an asyncio.Queue, reducing price latency
by ~50× for tighter entry precision.

Architecture:
    Thread: _tick_loop_sync()           Async event loop (LiveFeed consumer)
      for sym in symbols:    ──→    asyncio.Queue ──→  _ticks[sym] = latest
        mt5.symbol_info_tick()
      sleep(0.1s)

Usage:
    agg = TickAggregator(symbols=["XAUUSD"], poll_interval_ms=100)
    if await agg.start(asyncio.get_event_loop()):
        tick = await agg.get_tick()  # TickData | None

Falls back gracefully to yfinance polling if MT5 is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)

_QUEUE_MAX = 100  # max buffered ticks before dropping old ones


class TickAggregator:
    """
    Low-latency MT5 tick aggregator using a background thread.

    Parameters
    ----------
    symbols : list[str]
        Symbols to subscribe.
    poll_interval_ms : int
        Polling interval in milliseconds. Default 100ms (10 Hz).
    """

    def __init__(
        self,
        symbols: list[str],
        poll_interval_ms: int = 100,
    ) -> None:
        self._symbols = list(symbols)
        self._poll_interval = poll_interval_ms / 1000.0
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    async def start(self, loop: asyncio.AbstractEventLoop) -> bool:
        """
        Start the background tick thread.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The running event loop (needed for thread → async bridge).

        Returns
        -------
        bool
            True if started successfully (MT5 available), False otherwise.
        """
        try:
            import MetaTrader5 as mt5  # noqa: F401 — just test import
        except ImportError:
            logger.debug("[tick_agg] MT5 not installed — aggregator not started")
            return False

        self._loop = loop
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._tick_loop_sync,
            args=(self._stop_event, loop),
            daemon=True,
            name="tick-aggregator",
        )
        self._thread.start()
        self._running = True
        logger.info(
            "[tick_agg] Started | symbols=%s | interval=%dms",
            self._symbols, int(self._poll_interval * 1000),
        )
        return True

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
        self._running = False

    async def get_tick(self, timeout: float = 1.0):
        """
        Get the next tick from the queue.

        Returns
        -------
        TickData | None
        """
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal thread
    # ------------------------------------------------------------------

    def _tick_loop_sync(
        self,
        stop_event: threading.Event,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        Synchronous tick loop running in a background thread.

        Polls MT5 for each symbol at poll_interval_ms, then bridges
        the result into the asyncio event loop via
        asyncio.run_coroutine_threadsafe.
        """
        try:
            import MetaTrader5 as mt5
        except (ImportError, Exception):
            logger.debug("[tick_agg] MT5 unavailable in thread — exiting")
            return

        # Import here to avoid circular imports
        from alphaloop.data.live_feed import TickData

        logger.debug("[tick_agg] Thread started")

        while not stop_event.is_set():
            for symbol in self._symbols:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        td = TickData(
                            symbol=symbol,
                            bid=tick.bid,
                            ask=tick.ask,
                            spread=tick.ask - tick.bid,
                        )
                        # Bridge into asyncio — non-blocking put_nowait via coroutine
                        try:
                            asyncio.run_coroutine_threadsafe(
                                self._queue.put(td), loop
                            )
                        except Exception:
                            pass  # loop may be closing
                except Exception as e:
                    logger.debug("[tick_agg] Poll failed for %s: %s", symbol, e)

            time.sleep(self._poll_interval)

        logger.debug("[tick_agg] Thread stopped")
