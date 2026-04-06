from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.execution.service import ExecutionService


def _approval():
    return SimpleNamespace(
        approved=True,
        reason="",
        order_id="ord-1",
        client_order_id="cid-1",
        projected_risk_usd=150.0,
    )


def _signal():
    return SimpleNamespace(
        direction="BUY",
        entry_zone=(3100.0, 3102.0),
        setup_type="pullback",
        rr_ratio=1.5,
    )


@pytest.mark.asyncio
async def test_live_execution_blocks_when_pending_trade_log_write_fails():
    control_plane = SimpleNamespace(
        preflight=AsyncMock(return_value=_approval()),
        release_execution_lock=AsyncMock(),
    )
    executor = SimpleNamespace(open_order=AsyncMock())

    svc = ExecutionService(
        session_factory=AsyncMock(),
        executor=executor,
        control_plane=control_plane,
        supervision_service=SimpleNamespace(record_incident=AsyncMock()),
        dry_run=False,
    )
    svc._update_order_status = AsyncMock()
    svc._create_pending_trade_log = AsyncMock(return_value=None)
    svc._record_block_incident = AsyncMock()

    report = await svc.execute_market_order(
        symbol="XAUUSD",
        instance_id="inst-1",
        account_balance=10_000.0,
        signal=_signal(),
        sizing={"lots": 0.1, "risk_amount_usd": 150.0},
        stop_loss=3090.0,
        take_profit=3115.0,
        strategy_id="strat-1",
        is_dry_run=False,
    )

    assert report.status == "FAILED"
    assert "journal" in report.error_message.lower()
    executor.open_order.assert_not_awaited()
    control_plane.release_execution_lock.assert_awaited_once_with("XAUUSD", "inst-1")


@pytest.mark.asyncio
async def test_dry_run_allows_execution_without_pending_trade_log():
    control_plane = SimpleNamespace(
        preflight=AsyncMock(return_value=_approval()),
        release_execution_lock=AsyncMock(),
    )
    order_result = SimpleNamespace(
        success=True,
        order_ticket=12345,
        fill_price=3101.0,
        fill_volume=0.1,
        slippage_points=0.0,
        spread_at_fill=1.2,
        error_message="",
        executed_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    executor = SimpleNamespace(open_order=AsyncMock(return_value=order_result))

    svc = ExecutionService(
        session_factory=AsyncMock(),
        executor=executor,
        control_plane=control_plane,
        supervision_service=None,
        dry_run=True,
    )
    svc._update_order_status = AsyncMock()
    svc._create_pending_trade_log = AsyncMock(return_value=None)
    svc._create_trade_log = AsyncMock(return_value=42)

    report = await svc.execute_market_order(
        symbol="XAUUSD",
        instance_id="inst-1",
        account_balance=10_000.0,
        signal=_signal(),
        sizing={"lots": 0.1, "risk_amount_usd": 150.0},
        stop_loss=3090.0,
        take_profit=3115.0,
        strategy_id="strat-1",
        is_dry_run=True,
    )

    assert report.status == "FILLED"
    executor.open_order.assert_awaited_once()


def test_execution_service_normalizes_setup_aliases():
    signal = _signal()
    signal.setup_type = "range"

    assert ExecutionService._setup_type(signal) == "range_bounce"
