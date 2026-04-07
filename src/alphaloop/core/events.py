"""
Async event bus for decoupled inter-component communication.

Usage:
    bus = EventBus()
    bus.subscribe(SignalGenerated, my_handler)
    await bus.publish(SignalGenerated(symbol="XAUUSD", signal=...))
"""

from __future__ import annotations

import asyncio
import inspect
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
class CycleStarted(Event):
    """Fired at the very beginning of each trading cycle."""
    symbol: str = ""
    instance_id: str = ""
    cycle: int = 0


@dataclass
class CycleCompleted(Event):
    """Fired at the end of each trading cycle with the outcome."""
    symbol: str = ""
    instance_id: str = ""
    cycle: int = 0
    outcome: str = ""  # "no_signal" | "blocked" | "rejected" | "trade_opened" | "order_failed"
    detail: str = ""


@dataclass
class PipelineStep(Event):
    """Fired at each checkpoint in the trading cycle for granular visibility."""
    symbol: str = ""
    instance_id: str = ""
    cycle: int = 0
    stage: str = ""    # "risk_check" | "filters" | "signal_gen" | "validation" | "guards" | "sizing" | "execution"
    status: str = ""   # "passed" | "blocked" | "skipped" | "no_signal" | "generated" | "approved" | "rejected" | "filled" | "failed"
    detail: str = ""
    results: list = field(default_factory=list)  # per-tool ToolResult dicts (populated for "filters" stage)
    context: dict = field(default_factory=dict)  # structured diagnostic data (why a step failed)


@dataclass
class DirectionHypothesized(Event):
    """Emitted when a direction hypothesis is produced (before construction)."""
    symbol: str = ""
    direction: str = ""
    confidence: float = 0.0
    setup_tag: str = ""
    source_names: str = ""


@dataclass
class TradeConstructed(Event):
    """Emitted when a trade is successfully constructed from structure."""
    symbol: str = ""
    direction: str = ""
    sl_source: str = ""
    sl_distance_pts: float = 0.0
    rr_ratio: float = 0.0
    candidates_considered: int = 0


@dataclass
class ConstructionFailed(Event):
    """Emitted when trade construction fails (no valid SL from structure)."""
    symbol: str = ""
    direction: str = ""
    reason: str = ""
    candidates_considered: int = 0


@dataclass
class SignalGenerated(Event):
    symbol: str = ""
    instance_id: str = ""
    direction: str = ""
    confidence: float | None = None
    setup: str = ""
    signal_mode: str = ""
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
    validator_reasoning: str = ""
    rejection_reasons: list = field(default_factory=list)
    risk_score: float = 0.5


@dataclass
class TradeOpened(Event):
    symbol: str = ""
    direction: str = ""
    entry_price: float = 0.0
    lot_size: float = 0.0
    trade_id: int | None = None
    order_ticket: int | None = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0


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
    instance_id: str = ""


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
class StrategyVersionCreated(Event):
    """Fired when a new strategy version is created by autolearn."""
    symbol: str = ""
    version: int = 0
    source: str = ""   # "autolearn" | "manual"


@dataclass
class StrategyRolledBack(Event):
    """Fired when an autolearn version is rolled back."""
    symbol: str = ""
    instance_id: str = ""
    from_version: int = 0
    to_version: int = 0
    reason: str = ""


@dataclass
class ToolDecayAlert(Event):
    """Fired when a tool's predictive edge is decaying across rolling windows."""
    tool_name: str = ""
    short_wr: float = 0.0
    medium_wr: float = 0.0
    long_wr: float = 0.0
    deep_decay: bool = False
    samples: int = 0


@dataclass
class AlertTriggered(Event):
    """Fired when a system-level alert condition is detected (H-10 and similar).

    severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    """
    severity: str = "MEDIUM"
    rule_name: str = ""
    message: str = ""
    symbol: str = ""


@dataclass
class TradeRepositioned(Event):
    """Fired when an open trade is repositioned (SL tightened, partial/full close)."""
    symbol: str = ""
    instance_id: str = ""
    trade_id: int | None = None
    trigger: str = ""   # "opposite_signal" | "news_risk" | "volume_spike" | "volatility_spike"
    action: str = ""    # "tighten_sl" | "partial_close" | "full_close"
    reason: str = ""


# ── Type alias ────────────────────────────────────────────────────────────────
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


# ── Bus ───────────────────────────────────────────────────────────────────────

class EventBus:
    """
    Simple async event bus with publish/subscribe.

    Handlers are called concurrently via asyncio.gather.
    A failing handler logs the error but does not block other handlers.

    NOTE: Handlers should be idempotent or manage their own rollback.
    A handler failure does NOT trigger automatic cleanup of side effects
    from other handlers in the same publish() call.
    """

    # Max pending events before dropping (prevents memory exhaustion)
    MAX_QUEUE_SIZE = 1000

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[EventHandler]] = {}
        self._publish_count = 0
        self._drop_count = 0

    def subscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: type[Event], handler: EventHandler) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        event_type = type(event)

        # Collect handlers for this type AND all parent types (MRO)
        # so subscribing to Event catches all subclasses
        handlers: list[EventHandler] = []
        for cls in event_type.__mro__:
            handlers.extend(self._handlers.get(cls, []))
        if not handlers:
            return

        self._publish_count += 1

        tasks = []
        for handler in handlers:
            tasks.append(self._safe_call(handler, event))

        await asyncio.gather(*tasks)

    @property
    def stats(self) -> dict:
        return {
            "publish_count": self._publish_count,
            "drop_count": self._drop_count,
            "handler_count": sum(len(h) for h in self._handlers.values()),
            "event_types": len(self._handlers),
        }

    @staticmethod
    async def _safe_call(handler: EventHandler, event: Event) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except BaseException as exc:
            logger.exception(
                f"[event-bus] Handler {handler.__qualname__} failed for "
                f"{type(event).__name__}"
            )
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
