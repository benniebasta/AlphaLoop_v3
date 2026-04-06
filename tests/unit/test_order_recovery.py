"""Tests for startup order recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.db.repositories.order_repo import OrderRepository
from alphaloop.execution.recovery import OrderRecoveryWorker


@pytest.mark.asyncio
async def test_recovery_worker_promotes_broker_matched_orders(db_session):
    repo = OrderRepository(db_session)
    record = await repo.create(
        order_id="ord-match",
        symbol="XAUUSD",
        direction="BUY",
        lots=0.10,
        instance_id="test-instance",
        client_order_id="cid-match",
    )
    await repo.update_status(
        "ord-match",
        "SENT",
        broker_ticket=12345,
    )
    await db_session.commit()

    executor = AsyncMock()
    executor.get_open_positions.return_value = [
        SimpleNamespace(
            ticket=12345,
            entry_price=2010.5,
            volume=0.10,
        )
    ]

    worker = OrderRecoveryWorker(executor=executor, order_repo=repo)
    report = await worker.recover_startup_orders(instance_id="test-instance")

    reloaded = await repo.get_by_order_id(record.order_id)
    assert report.resolved_orders == 1
    assert report.unresolved_orders == 0
    assert report.has_critical is False
    assert reloaded is not None
    assert reloaded.status == "FILLED"
    assert reloaded.broker_ticket == 12345
    assert reloaded.fill_price == 2010.5


@pytest.mark.asyncio
async def test_recovery_worker_marks_unmatched_orders_recovery_pending(db_session):
    repo = OrderRepository(db_session)
    await repo.create(
        order_id="ord-unresolved",
        symbol="XAUUSD",
        direction="BUY",
        lots=0.10,
        instance_id="test-instance",
        client_order_id="cid-unresolved",
    )
    await db_session.commit()

    executor = AsyncMock()
    executor.get_open_positions.return_value = []

    worker = OrderRecoveryWorker(executor=executor, order_repo=repo)
    report = await worker.recover_startup_orders(instance_id="test-instance")

    reloaded = await repo.get_by_order_id("ord-unresolved")
    assert report.resolved_orders == 0
    assert report.unresolved_orders == 1
    assert report.has_critical is True
    assert reloaded is not None
    assert reloaded.status == "RECOVERY_PENDING"
    assert "cannot be verified after restart" in (reloaded.error_message or "")


@pytest.mark.asyncio
async def test_recovery_worker_blocks_when_broker_query_fails(db_session):
    repo = OrderRepository(db_session)
    await repo.create(
        order_id="ord-broker-fail",
        symbol="XAUUSD",
        direction="BUY",
        lots=0.10,
        instance_id="test-instance",
    )
    await db_session.commit()

    executor = AsyncMock()
    executor.get_open_positions.side_effect = RuntimeError("broker timeout")

    worker = OrderRecoveryWorker(executor=executor, order_repo=repo)
    report = await worker.recover_startup_orders(instance_id="test-instance")

    assert report.resolved_orders == 0
    assert report.unresolved_orders == 1
    assert report.has_critical is True
    assert report.issues[0].issue_type == "broker_query_failed"
