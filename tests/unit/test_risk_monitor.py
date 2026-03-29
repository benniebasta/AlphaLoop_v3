"""Tests for RiskMonitor."""

import pytest
from alphaloop.risk.monitor import RiskMonitor


@pytest.mark.asyncio
async def test_risk_monitor_basic():
    rm = RiskMonitor(10000.0)
    await rm.seed_from_db()
    can, reason = await rm.can_open_trade()
    assert can is True


@pytest.mark.asyncio
async def test_risk_monitor_max_concurrent():
    rm = RiskMonitor(10000.0, max_concurrent_trades=1)
    await rm.seed_from_db()
    await rm.register_open(100.0)
    can, reason = await rm.can_open_trade()
    assert can is False
    assert "concurrent" in reason.lower()


@pytest.mark.asyncio
async def test_risk_monitor_kill_switch():
    rm = RiskMonitor(10000.0, consecutive_loss_limit=2)
    await rm.seed_from_db()
    await rm.record_trade_close(-100.0)
    await rm.record_trade_close(-100.0)
    can, reason = await rm.can_open_trade()
    assert can is False
    assert "kill" in reason.lower()


@pytest.mark.asyncio
async def test_risk_monitor_not_seeded():
    rm = RiskMonitor(10000.0)
    can, reason = await rm.can_open_trade()
    assert can is False
    assert "seeded" in reason.lower()


@pytest.mark.asyncio
async def test_risk_monitor_status():
    rm = RiskMonitor(10000.0)
    await rm.seed_from_db()
    status = rm.status
    assert status["seeded"] is True
    assert status["kill_switch"] is False
    assert status["open_trades"] == 0
