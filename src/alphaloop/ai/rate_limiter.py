"""
ai/rate_limiter.py
Async per-provider sliding-window rate limiter.

Limits the number of AI calls per provider within a configurable time window
to prevent credit exhaustion and API ban.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from alphaloop.core.errors import RateLimitError


class AsyncRateLimiter:
    """
    Sliding-window rate limiter keyed by provider name.

    Parameters
    ----------
    max_calls : int
        Maximum calls allowed per window per provider.
    window_seconds : float
        Length of the sliding window in seconds.
    """

    def __init__(self, max_calls: int = 10, window_seconds: float = 60.0) -> None:
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, provider: str) -> None:
        """
        Acquire a rate-limit slot for *provider*.

        Raises RateLimitError if the limit is exceeded.
        """
        lock = self._locks[provider]
        async with lock:
            now = time.monotonic()
            dq = self._calls[provider]

            # Evict timestamps outside the window
            while dq and (now - dq[0]) > self._window:
                dq.popleft()

            if len(dq) >= self._max_calls:
                wait = self._window - (now - dq[0])
                raise RateLimitError(
                    f"Rate limit exceeded for provider '{provider}': "
                    f"{self._max_calls} calls/{self._window}s. "
                    f"Retry in {wait:.1f}s."
                )

            dq.append(now)

    async def acquire_or_wait(self, provider: str, timeout: float = 60.0) -> None:
        """
        Acquire a slot, waiting up to *timeout* seconds if the limit is hit.

        Raises RateLimitError if still blocked after timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                await self.acquire(provider)
                return
            except RateLimitError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                await asyncio.sleep(min(1.0, remaining))

    def reset(self, provider: str | None = None) -> None:
        """Clear rate-limit state. If provider is None, clear all."""
        if provider is None:
            self._calls.clear()
        else:
            self._calls.pop(provider, None)
