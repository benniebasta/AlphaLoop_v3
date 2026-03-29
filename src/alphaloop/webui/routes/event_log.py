"""GET /api/events — Recent event bus activity."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/events", tags=["events"])
logger = logging.getLogger(__name__)

_event_log: deque[dict] = deque(maxlen=200)


def record_event(event) -> None:
    """Called by event bus handler to log events into the ring buffer."""
    _event_log.appendleft(
        {
            "type": type(event).__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                k: v for k, v in vars(event).items() if not k.startswith("_")
            },
        }
    )


@router.get("")
async def get_events(
    limit: int = Query(default=50, le=200),
    event_type: str | None = Query(default=None, alias="type"),
) -> dict:
    """Return recent events, optionally filtered by type."""
    events = list(_event_log)
    if event_type:
        events = [e for e in events if e["type"] == event_type]
    return {"events": events[:limit], "total": len(_event_log)}
