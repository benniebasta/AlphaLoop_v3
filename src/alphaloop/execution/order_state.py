"""
Order lifecycle state machine.

States:
  PENDING     — order created, not yet sent to broker
  SENT        — sent to broker, awaiting acknowledgment
  FILLED      — fully filled at broker
  PARTIAL     — partially filled
  CANCELLED   — cancelled before fill
  REJECTED    — broker rejected the order
  FAILED      — internal error during submission

Tracks state transitions with timestamps for audit.
"""

import logging
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OrderState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    SENT = "SENT"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    RECOVERY_PENDING = "RECOVERY_PENDING"  # Phase 5A: restart found unresolved order


class OrderTransition(BaseModel):
    from_state: OrderState
    to_state: OrderState
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""


class OrderTracker(BaseModel):
    """Tracks a single order through its lifecycle."""

    order_id: str
    symbol: str
    direction: str
    lots: float
    state: OrderState = OrderState.PENDING
    transitions: list[OrderTransition] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Fill details
    broker_ticket: int | None = None
    requested_price: float = 0.0
    fill_price: float | None = None
    fill_volume: float | None = None
    slippage_points: float = 0.0
    spread_at_fill: float = 0.0

    # Error info
    error_code: int | None = None
    error_message: str = ""

    # Verification
    verified: bool = False
    verification_attempts: int = 0

    _VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
        OrderState.PENDING: {OrderState.APPROVED, OrderState.SENT, OrderState.FAILED, OrderState.CANCELLED},
        OrderState.APPROVED: {
            OrderState.SENT,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.FAILED,
        },
        OrderState.SENT: {
            OrderState.FILLED,
            OrderState.PARTIAL,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.FAILED,
        },
        OrderState.PARTIAL: {OrderState.FILLED, OrderState.CANCELLED},
        OrderState.FILLED: set(),  # Terminal
        OrderState.CANCELLED: set(),  # Terminal
        OrderState.REJECTED: set(),  # Terminal
        OrderState.FAILED: {OrderState.PENDING},  # Can retry
        OrderState.RECOVERY_PENDING: {  # Phase 5A
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.FAILED,
        },
    }

    def transition(self, new_state: OrderState, reason: str = "") -> bool:
        """Attempt a state transition. Returns True if valid."""
        valid = self._VALID_TRANSITIONS.get(self.state, set())
        if new_state not in valid:
            logger.warning(
                "[order-state] Invalid transition %s -> %s for order %s (reason: %s)",
                self.state, new_state, self.order_id, reason,
            )
            return False

        self.transitions.append(
            OrderTransition(
                from_state=self.state,
                to_state=new_state,
                reason=reason,
            )
        )
        self.state = new_state
        return True

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
        )

    @property
    def is_active(self) -> bool:
        return self.state in (
            OrderState.PENDING,
            OrderState.APPROVED,
            OrderState.SENT,
            OrderState.PARTIAL,
        )

    def mark_sent(self, broker_ticket: int) -> None:
        """Mark order as sent to broker."""
        if self.transition(OrderState.SENT, f"ticket={broker_ticket}"):
            self.broker_ticket = broker_ticket

    def mark_filled(
        self,
        fill_price: float,
        fill_volume: float,
        slippage: float = 0.0,
        spread: float = 0.0,
    ) -> None:
        """Mark order as filled."""
        if self.transition(OrderState.FILLED, f"price={fill_price}, vol={fill_volume}"):
            self.fill_price = fill_price
            self.fill_volume = fill_volume
            self.slippage_points = slippage
            self.spread_at_fill = spread

    def mark_rejected(self, error_code: int = 0, error_message: str = "") -> None:
        """Mark order as rejected by broker."""
        if self.transition(OrderState.REJECTED, error_message):
            self.error_code = error_code
            self.error_message = error_message

    def mark_failed(self, error_message: str = "") -> None:
        """Mark order as failed (internal error)."""
        if self.transition(OrderState.FAILED, error_message):
            self.error_message = error_message

    def mark_verified(self) -> None:
        """Mark that fill was verified with broker."""
        self.verified = True
        self.verification_attempts += 1
        logger.info(
            "[order-state] Order %s verified at fill_price=%.5f vol=%.4f",
            self.order_id, self.fill_price or 0, self.fill_volume or 0,
        )


def compute_client_order_id(
    signal_id: str,
    symbol: str,
    direction: str,
    timestamp_bucket: str,
    strategy_id: str = "",
) -> str:
    """Phase 5C: Deterministic client order ID via sha256.

    Do NOT use Python hash() — it is process-randomized (PYTHONHASHSEED).
    """
    import hashlib
    raw = f"{signal_id}|{symbol}|{direction}|{timestamp_bucket}|{strategy_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class OrderRegistry:
    """In-memory registry of active order trackers.

    Phase 5B: Optionally accepts an OrderRepository for write-through
    persistence on every state transition.
    """

    def __init__(self, order_repo=None) -> None:
        self._orders: dict[str, OrderTracker] = {}
        self._by_ticket: dict[int, str] = {}
        self._repo = order_repo  # Phase 5B: optional DB write-through

    async def reload_from_db(self) -> int:
        """Phase 5B: Reload active/recovery-pending orders from DB on startup."""
        if not self._repo:
            return 0
        non_terminal = await self._repo.get_non_terminal()
        loaded = 0
        for record in non_terminal:
            tracker = OrderTracker(
                order_id=record.order_id,
                symbol=record.symbol,
                direction=record.direction,
                lots=record.lots,
                state=OrderState.RECOVERY_PENDING,
                broker_ticket=record.broker_ticket,
                fill_price=record.fill_price,
                fill_volume=record.fill_volume,
            )
            self._orders[record.order_id] = tracker
            if record.broker_ticket:
                self._by_ticket[record.broker_ticket] = record.order_id
            loaded += 1
        if loaded:
            logger.info(
                "[order-registry] Reloaded %d non-terminal orders from DB", loaded,
            )
        return loaded

    def create(
        self,
        order_id: str,
        symbol: str,
        direction: str,
        lots: float,
        requested_price: float = 0.0,
    ) -> OrderTracker:
        tracker = OrderTracker(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            requested_price=requested_price,
        )
        self._orders[order_id] = tracker
        return tracker

    def get(self, order_id: str) -> OrderTracker | None:
        return self._orders.get(order_id)

    def get_by_ticket(self, ticket: int) -> OrderTracker | None:
        oid = self._by_ticket.get(ticket)
        return self._orders.get(oid) if oid else None

    def register_ticket(self, order_id: str, ticket: int) -> None:
        self._by_ticket[ticket] = order_id

    def get_active(self) -> list[OrderTracker]:
        return [o for o in self._orders.values() if o.is_active]

    def get_unverified(self) -> list[OrderTracker]:
        return [
            o for o in self._orders.values()
            if o.state == OrderState.FILLED and not o.verified
        ]

    def cleanup_terminal(self, max_keep: int = 100) -> int:
        """Remove old terminal orders, keeping the most recent."""
        terminal = [
            (o.order_id, o.created_at)
            for o in self._orders.values()
            if o.is_terminal
        ]
        if len(terminal) <= max_keep:
            return 0
        terminal.sort(key=lambda x: x[1])
        to_remove = terminal[: len(terminal) - max_keep]
        for oid, _ in to_remove:
            tracker = self._orders.pop(oid, None)
            if tracker and tracker.broker_ticket:
                self._by_ticket.pop(tracker.broker_ticket, None)
        return len(to_remove)
