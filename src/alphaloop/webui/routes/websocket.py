"""WebSocket /ws — real-time updates via event bus."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from alphaloop.core.events import Event

router = APIRouter(tags=["websocket"])
logger = logging.getLogger(__name__)

# Connected WebSocket clients
_clients: set[WebSocket] = set()
_subscribed: bool = False


async def broadcast(data: dict) -> None:
    """Send a JSON message to all connected clients."""
    if not _clients:
        return
    text = json.dumps(data, default=str)
    stale: list[WebSocket] = []
    for ws in _clients:
        try:
            await ws.send_text(text)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _clients.discard(ws)


async def _event_handler(event: Event) -> None:
    """Forward EventBus events to all WebSocket clients and record to event log."""
    # Record into the in-memory event log ring buffer
    from alphaloop.webui.routes.event_log import record_event

    record_event(event)

    data = {
        "type": type(event).__name__,
        "timestamp": event.timestamp.isoformat(),
    }
    try:
        data.update(asdict(event))
    except Exception:
        pass
    # Ensure timestamp is serialisable
    data["timestamp"] = str(data.get("timestamp", ""))
    await broadcast(data)


def _check_ws_token(ws: WebSocket) -> bool:
    """Validate WebSocket auth token from query params."""
    token = os.environ.get("AUTH_TOKEN", "")
    if not token:
        return True  # No auth in dev mode
    provided = ws.query_params.get("token", "")
    return provided == token


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Accept WebSocket connections and forward EventBus events."""
    if not _check_ws_token(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    _clients.add(ws)

    # Subscribe to all events via the container's EventBus (once only)
    global _subscribed
    if not _subscribed:
        container = ws.app.state.container
        event_bus = container.event_bus
        event_bus.subscribe(Event, _event_handler)
        _subscribed = True

    logger.info("[ws] Client connected (%d total)", len(_clients))

    try:
        while True:
            # Keep connection alive — read client pings/messages
            data = await ws.receive_text()
            # Echo ping back as pong
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("[ws] Client disconnected with error")
    finally:
        _clients.discard(ws)
        logger.info("[ws] Client disconnected (%d remaining)", len(_clients))
