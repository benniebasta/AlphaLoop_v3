"""
Async event bus for decoupled inter-component communication.

Usage:
    bus = EventBus()
    bus.subscribe(SignalGenerated, my_handler)
    await bus.publish(SignalGenerated(symbol="XAUUSD", signal=...))
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


# ── Event types ───────────────────────────────────────────────────────────────

@dataclass
class Event:
    """Base event — all events carry a timestamp."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SignalGenerated(Event):
    symbol: str = ""
    signal: Any = None


@dataclass
class SignalValidated(Event):
    symbol: str = ""
    signal: Any = None
    approved: bool = False


@dataclass
class SignalRejected(Event):
    symbol: str = ""
    reason: str = ""
    rejected_by: str = ""


@dataclass
class TradeOpened(Event):
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    lot_size: float = 0.0
    trade_id: int | None = None


@dataclass
class TradeClosed(Event):
    symbol: str = ""
    outcome: str = ""
    pnl_usd: float = 0.0
    trade_id: int | None = None


@dataclass
class PipelineBlocked(Event):
    symbol: str = ""
    blocked_by: str = ""
    reason: str = ""


@dataclass
class RiskLimitHit(Event):
    symbol: str = ""
    limit_type: str = ""
    details: str = ""


@dataclass
class ResearchCompleted(Event):
    symbol: str = ""
    report_id: int | None = None


@dataclass
class ConfigChanged(Event):
    keys: list[str] = field(default_factory=list)
    source: str = ""  # "webui" | "autolearn" | "startup"


@dataclass
class StrategyPromoted(Event):
    """Fired when a strategy version is promoted to a new stage."""
    symbol: str = ""
    version: int = 0
    from_status: str = ""
    to_status: str = ""


@dataclass
class SeedLabProgress(Event):
    """Fired during SeedLab pipeline execution for progress tracking."""
    run_id: str = ""
    phase: str = ""  # "generating_seeds" | "evaluating" | "ranking" | "completed" | "failed"
    current: int = 0
    total: int = 0
    message: str = ""


@dataclass
class CanaryStarted(Event):
    """Fired when a canary deployment begins."""
    symbol: str = ""
    canary_id: str = ""
    allocation_pct: float = 0.0
    duration_hours: int = 0


@dataclass
class CanaryEnded(Event):
    """Fired when a canary deployment ends."""
    symbol: str = ""
    canary_id: str = ""
    recommendation: str = ""  # "promote" | "reject"


@dataclass
class MetaLoopCompleted(Event):
    """Fired when a meta-loop cycle finishes."""
    symbol: str = ""
    action_taken: str = ""  # "none" | "research_completed" | "optimized" | "rolled_back"
    new_version: int | None = None
    details: str = ""


@dataclass
class StrategyRolledBack(Event):
    """Fired when an autolearn version is rolled back."""
    symbol: str = ""
    from_version: int = 0
    to_version: int = 0
    reason: str = ""


# ── Type alias ────────────────────────────────────────────────────────────────
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


# ── Bus ───────────────────────────────────────────────────────────────────────

class EventBus:
    """
    Simple async event bus with publish/subscribe.

    Handlers are called concurrently via asyncio.gather.
    A failing handler logs the error but does not block other handlers.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[EventHandler]] = {}

    def subscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        tasks = []
        for handler in handlers:
            tasks.append(self._safe_call(handler, event))

        await asyncio.gather(*tasks)

    @staticmethod
    async def _safe_call(handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except BaseException as exc:
            logger.exception(
                f"[event-bus] Handler {handler.__qualname__} failed for "
                f"{type(event).__name__}"
            )
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
