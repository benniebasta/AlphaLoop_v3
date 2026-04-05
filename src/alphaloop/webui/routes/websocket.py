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
        except Exception as e:
            logger.debug("[ws] Failed to send to client: %s", e)
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
    except Exception as e:
        logger.debug("[ws] asdict failed for event %s: %s", type(event).__name__, e)
    # Ensure timestamp is serialisable
    data["timestamp"] = str(data.get("timestamp", ""))
    await broadcast(data)


def _check_ws_token(ws: WebSocket) -> tuple[bool, str]:
    """
    Validate WebSocket auth token.

    Token resolution order (most-to-least secure):
      1. Sec-WebSocket-Protocol header — sent by frontend as `new WebSocket(url, [token])`.
         Not visible in server logs or browser history unlike query params.
      2. ?token= query param — legacy fallback for backward compatibility.

    Returns (valid: bool, provided_token: str).
    """
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return True, ""  # No auth configured — dev mode
    # Primary: subprotocol header (secure)
    provided = ws.headers.get("sec-websocket-protocol", "").strip()
    if not provided:
        # Fallback: query param (legacy, logs a deprecation warning)
        provided = ws.query_params.get("token", "")
        if provided:
            logger.warning(
                "[ws] Auth token passed via query param — migrate to "
                "Sec-WebSocket-Protocol header (new WebSocket(url, [token]))"
            )
    return provided == expected, provided


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    """Accept WebSocket connections and forward EventBus events."""
    valid, provided_token = _check_ws_token(ws)
    if not valid:
        await ws.close(code=4001, reason="Unauthorized")
        return

    # Echo back the subprotocol so the browser does not close the connection
    # (RFC 6455 §4.2.2: server must match a requested subprotocol or omit).
    subproto = provided_token if ws.headers.get("sec-websocket-protocol") else None
    await ws.accept(subprotocol=subproto)
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
