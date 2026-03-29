"""Health heartbeat writer — periodic status updates."""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class HeartbeatWriter:
    """Writes periodic heartbeat files for external monitoring."""

    def __init__(self, path: str | Path = "heartbeat.json", interval_sec: float = 60.0):
        self._path = Path(path)
        self._interval = interval_sec
        self._last_write = 0.0

    def write(self, status: dict) -> None:
        """Write heartbeat if interval has elapsed."""
        now = time.time()
        if now - self._last_write < self._interval:
            return
        self._last_write = now
        try:
            data = {
                "timestamp": now,
                "alive": True,
                **status,
            }
            self._path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as e:
            logger.warning("Heartbeat write failed: %s", e)
