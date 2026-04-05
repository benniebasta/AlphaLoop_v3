"""Notification dispatcher with batching and dedup."""

import asyncio
import hashlib
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """
    Batches and deduplicates notifications before sending.
    Prevents alert spam during fast-moving market conditions.
    """

    def __init__(
        self,
        sender,
        *,
        flush_interval_sec: float = 60.0,
        dedup_window_sec: float = 300.0,
    ):
        self._sender = sender
        self._queue: list[str] = []
        self._flush_interval = flush_interval_sec
        self._dedup_window = dedup_window_sec
        self._recent_hashes: deque[tuple[float, str]] = deque(maxlen=100)
        self._last_flush = time.time()

    async def enqueue(self, message: str) -> None:
        """Add a message to the queue, skipping duplicates."""
        msg_hash = hashlib.sha256(message.encode()).hexdigest()[:16]
        now = time.time()

        # Check dedup
        cutoff = now - self._dedup_window
        self._recent_hashes = deque(
            ((t, h) for t, h in self._recent_hashes if t > cutoff),
            maxlen=100,
        )
        if any(h == msg_hash for _, h in self._recent_hashes):
            return

        self._recent_hashes.append((now, msg_hash))
        self._queue.append(message)

        # Auto-flush if interval passed
        if now - self._last_flush >= self._flush_interval:
            await self.flush()

    async def flush(self) -> None:
        """Send all queued messages."""
        if not self._queue:
            return

        messages = self._queue.copy()
        self._queue.clear()
        self._last_flush = time.time()

        for msg in messages:
            try:
                await self._sender.send(msg)
            except Exception as e:
                logger.warning("Notification send failed: %s", e)
