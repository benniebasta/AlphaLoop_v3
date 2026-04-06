"""Extended tests for RiskMonitor state transitions."""

from __future__ import annotations

from datetime import timedelta

import pytest

from alphaloop.risk.monitor import RiskMonitor


@pytest.fixture
def monitor() -> RiskMonitor:
    """Fresh RiskMonitor with $10,000 balance, seeded."""
    m = RiskMonitor(10_000.0)
    m._seeded = True  # skip DB seeding for unit tests
    return m


# ── Balance updates ──────────────────────────────────────────────────────────


def test_update_balance():
    """update_balance() changes account_balance."""
    m = RiskMonitor(10_000.0)
    m.update_balance(12_000.0)
    assert m.account_balance == 12_000.0


def test_update_balance_to_zero():
    """update_balance(0) sets balance to zero."""
    m = RiskMonitor(5_000.0)
    m.update_balance(0.0)
    assert m.account_balance == 0.0


# ── Consecutive loss tracking ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consecutive_losses_increment(monitor):
    """Each negative pnl increments _consecutive_losses."""
    await monitor.record_trade_close(-50.0)
    assert monitor._consecutive_losses == 1
    await monitor.record_trade_close(-30.0)
    assert monitor._consecutive_losses == 2


@pytest.mark.asyncio
async def test_breakeven_resets_consecutive(monitor):
    """A breakeven trade (pnl=0) resets consecutive losses to 0."""
    await monitor.record_trade_close(-100.0)
    await monitor.record_trade_close(-100.0)
    assert monitor._consecutive_losses == 2
    await monitor.record_trade_close(0.0)  # breakeven
    assert monitor._consecutive_losses == 0


@pytest.mark.asyncio
async def test_winning_trade_resets_consecutive(monitor):
    """A winning trade resets consecutive losses to 0."""
    await monitor.record_trade_close(-50.0)
    await monitor.record_trade_close(-50.0)
    await monitor.record_trade_close(-50.0)
    assert monitor._consecutive_losses == 3
    await monitor.record_trade_close(100.0)  # win
    assert monitor._consecutive_losses == 0


# ── Balance adjustment on trade close ────────────────────────────────────────


@pytest.mark.asyncio
async def test_balance_updates_after_trade_close(monitor):
    """account_balance is adjusted by pnl on each trade close."""
    initial = monitor.account_balance
    await monitor.record_trade_close(-200.0)
    assert monitor.account_balance == initial - 200.0
    await monitor.record_trade_close(150.0)
    assert monitor.account_balance == initial - 200.0 + 150.0


# ── Kill switch / can_open_trade ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consecutive_loss_limit_blocks_trades(monitor):
    """After consecutive_loss_limit losses, can_open_trade returns False."""
    for _ in range(monitor.consecutive_loss_limit):
        await monitor.record_trade_close(-10.0)
    assert monitor._consecutive_losses == monitor.consecutive_loss_limit
    allowed, reason = await monitor.can_open_trade()
    assert allowed is False
    assert "Kill switch" in reason


@pytest.mark.asyncio
async def test_daily_loss_limit_activates_kill_switch():
    """Large daily losses activate the kill switch."""
    m = RiskMonitor(10_000.0, max_daily_loss_pct=0.03)
    m._seeded = True
    # Lose 3% of balance in one trade
    await m.record_trade_close(-310.0)
    # The kill switch should be active because daily loss >= 3%
    allowed, reason = await m.can_open_trade()
    assert allowed is False
    assert m._kill_switch_active is True


@pytest.mark.asyncio
async def test_can_open_trade_when_healthy(monitor):
    """can_open_trade returns True when risk limits are fine."""
    allowed, reason = await monitor.can_open_trade()
    assert allowed is True
    assert reason == ""


@pytest.mark.asyncio
async def test_not_seeded_blocks_trades():
    """An un-seeded RiskMonitor blocks all trades."""
    m = RiskMonitor(10_000.0)
    # _seeded defaults to False
    allowed, reason = await m.can_open_trade()
    assert allowed is False
    assert "not seeded" in reason.lower()


@pytest.mark.asyncio
async def test_day_rollover_preserves_kill_switch_and_force_close():
    """Day rollover must not silently clear a live kill switch."""
    m = RiskMonitor(10_000.0, consecutive_loss_limit=1)
    m._seeded = True
    await m.record_trade_close(-100.0)
    assert m.kill_switch_active is True
    assert m.force_close_all is True

    m.today = m.today - timedelta(days=1)
    allowed, reason = await m.can_open_trade()

    assert allowed is False
    assert "kill switch" in reason.lower()
    assert m.kill_switch_active is True
    assert m.force_close_all is True
    assert m._daily_pnl == 0.0


@pytest.mark.asyncio
async def test_no_new_risk_blocks_new_trades(monitor):
    """No-new-risk mode should block entries without relying on kill switch."""
    monitor.activate_no_new_risk("reconciler_failure")

    allowed, reason = await monitor.can_open_trade()

    assert allowed is False
    assert "no new risk" in reason.lower()
    assert "reconciler_failure" in reason
    assert monitor.no_new_risk_active is True


@pytest.mark.asyncio
async def test_no_new_risk_requires_all_reasons_to_clear(monitor):
    """Compound no-new-risk state remains active until all reasons clear."""
    monitor.activate_no_new_risk("reconciler_failure")
    monitor.activate_no_new_risk("broker_db_split_brain")

    cleared = monitor.clear_no_new_risk_reason("reconciler_failure")
    allowed, reason = await monitor.can_open_trade()

    assert cleared is True
    assert allowed is False
    assert monitor.no_new_risk_active is True
    assert monitor.no_new_risk_reasons == ("broker_db_split_brain",)
    assert "broker_db_split_brain" in reason

    cleared_final = monitor.clear_no_new_risk_reason("broker_db_split_brain")
    allowed_after, reason_after = await monitor.can_open_trade()

    assert cleared_final is True
    assert allowed_after is True
    assert reason_after == ""
    assert monitor.no_new_risk_active is False
    assert monitor.no_new_risk_reasons == ()
