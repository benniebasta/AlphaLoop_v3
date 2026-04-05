"""Tests for cross-instance risk aggregation."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from alphaloop.risk.cross_instance import CrossInstanceRiskAggregator


@pytest.fixture
def mock_trade_repo():
    repo = AsyncMock()
    repo.get_open_trades = AsyncMock(return_value=[])
    repo.get_closed_trades = AsyncMock(return_value=[])
    return repo


@pytest.mark.asyncio
async def test_empty_portfolio_allows_trade(mock_trade_repo):
    agg = CrossInstanceRiskAggregator(trade_repo=mock_trade_repo)
    allowed, reason = await agg.can_open_trade(10000.0, 100.0)
    assert allowed
    assert reason == ""


@pytest.mark.asyncio
async def test_position_cap_blocks_trade(mock_trade_repo):
    # Simulate 6 open trades
    trades = [MagicMock(risk_amount_usd=100) for _ in range(6)]
    mock_trade_repo.get_open_trades = AsyncMock(return_value=trades)

    agg = CrossInstanceRiskAggregator(
        trade_repo=mock_trade_repo,
        max_total_open_positions=6,
    )
    allowed, reason = await agg.can_open_trade(10000.0)
    assert not allowed
    assert "position cap" in reason.lower()


@pytest.mark.asyncio
async def test_no_repo_blocks_trade_by_default():
    """Phase 3C: fail-closed when aggregation unavailable (default)."""
    agg = CrossInstanceRiskAggregator(trade_repo=None)
    allowed, reason = await agg.can_open_trade(10000.0)
    assert not allowed  # Fail-closed by default
    assert "unavailable" in reason.lower()


@pytest.mark.asyncio
async def test_no_repo_allows_trade_when_fail_open():
    """Explicit fail_open=True allows trade when aggregation unavailable."""
    agg = CrossInstanceRiskAggregator(trade_repo=None, fail_open=True)
    allowed, reason = await agg.can_open_trade(10000.0)
    assert allowed


@pytest.mark.asyncio
async def test_aggregate_status(mock_trade_repo):
    agg = CrossInstanceRiskAggregator(trade_repo=mock_trade_repo)
    status = await agg.get_aggregate_status(10000.0)
    assert status["available"]
    assert status["total_open_positions"] == 0
    assert status["total_daily_pnl"] == 0.0
