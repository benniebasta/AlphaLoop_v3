"""Tests for order state machine."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.execution.order_state import OrderState, OrderTracker, OrderRegistry


def test_order_lifecycle_happy_path():
    tracker = OrderTracker(
        order_id="test-001",
        symbol="XAUUSD",
        direction="BUY",
        lots=0.10,
        requested_price=2000.0,
    )
    assert tracker.state == OrderState.PENDING
    assert tracker.is_active
    assert not tracker.is_terminal

    tracker.mark_sent(broker_ticket=12345)
    assert tracker.state == OrderState.SENT
    assert tracker.broker_ticket == 12345

    tracker.mark_filled(fill_price=2000.50, fill_volume=0.10, slippage=0.50)
    assert tracker.state == OrderState.FILLED
    assert tracker.is_terminal
    assert tracker.fill_price == 2000.50
    assert tracker.slippage_points == 0.50
    assert len(tracker.transitions) == 2


def test_order_rejection():
    tracker = OrderTracker(
        order_id="test-002",
        symbol="XAUUSD",
        direction="SELL",
        lots=0.05,
    )
    tracker.transition(OrderState.SENT)
    tracker.mark_rejected(error_code=10004, error_message="Insufficient margin")
    assert tracker.state == OrderState.REJECTED
    assert tracker.is_terminal
    assert tracker.error_code == 10004


def test_invalid_transition():
    tracker = OrderTracker(
        order_id="test-003",
        symbol="XAUUSD",
        direction="BUY",
        lots=0.10,
    )
    tracker.mark_filled(fill_price=2000.0, fill_volume=0.10)
    # PENDING -> FILLED is not valid (must go through SENT)
    assert tracker.state == OrderState.PENDING


def test_terminal_state_blocks_transitions():
    tracker = OrderTracker(
        order_id="test-004",
        symbol="XAUUSD",
        direction="BUY",
        lots=0.10,
    )
    tracker.transition(OrderState.SENT)
    tracker.mark_filled(fill_price=2000.0, fill_volume=0.10)
    # Cannot transition from FILLED
    result = tracker.transition(OrderState.CANCELLED)
    assert not result
    assert tracker.state == OrderState.FILLED


def test_order_registry():
    registry = OrderRegistry()
    t1 = registry.create("ord-1", "XAUUSD", "BUY", 0.10, 2000.0)
    t2 = registry.create("ord-2", "EURUSD", "SELL", 0.05, 1.1000)

    assert registry.get("ord-1") is t1
    assert registry.get("ord-2") is t2
    assert registry.get("ord-3") is None

    assert len(registry.get_active()) == 2

    t1.transition(OrderState.SENT)
    t1.transition(OrderState.FILLED)
    assert len(registry.get_active()) == 1
    assert len(registry.get_unverified()) == 1

    t1.mark_verified()
    assert len(registry.get_unverified()) == 0


def test_registry_ticket_lookup():
    registry = OrderRegistry()
    t1 = registry.create("ord-1", "XAUUSD", "BUY", 0.10)
    registry.register_ticket("ord-1", 99999)

    found = registry.get_by_ticket(99999)
    assert found is t1
    assert registry.get_by_ticket(11111) is None


@pytest.mark.asyncio
async def test_registry_reload_from_db_marks_non_terminal_orders_for_recovery():
    repo = SimpleNamespace(
        get_non_terminal=AsyncMock(
            return_value=[
                SimpleNamespace(
                    order_id="ord-9",
                    symbol="XAUUSD",
                    direction="BUY",
                    lots=0.10,
                    broker_ticket=77777,
                    fill_price=None,
                    fill_volume=None,
                )
            ]
        )
    )
    registry = OrderRegistry(order_repo=repo)

    loaded = await registry.reload_from_db()

    assert loaded == 1
    tracker = registry.get("ord-9")
    assert tracker is not None
    assert tracker.state == OrderState.RECOVERY_PENDING
    assert registry.get_by_ticket(77777) is tracker
