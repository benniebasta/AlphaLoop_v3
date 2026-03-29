"""Tests for async repository operations."""

import pytest
from sqlalchemy import select

from alphaloop.db.models.trade import TradeLog
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.db.repositories.trade_repo import TradeRepository


@pytest.mark.asyncio
async def test_settings_repo_crud(db_session):
    repo = SettingsRepository(db_session)
    await repo.set("MY_KEY", "my_value")
    await db_session.flush()

    val = await repo.get("MY_KEY")
    assert val == "my_value"

    val_missing = await repo.get("MISSING", "default")
    assert val_missing == "default"


@pytest.mark.asyncio
async def test_settings_repo_set_many(db_session):
    repo = SettingsRepository(db_session)
    await repo.set_many({"A": "1", "B": "2"})
    await db_session.flush()

    all_settings = await repo.get_all()
    assert all_settings["A"] == "1"
    assert all_settings["B"] == "2"


@pytest.mark.asyncio
async def test_trade_repo_create_and_query(db_session):
    repo = TradeRepository(db_session)
    trade = await repo.create(
        symbol="XAUUSD",
        direction="BUY",
        outcome="OPEN",
        instance_id="test_bot",
        entry_price=2340.0,
        lot_size=0.1,
    )
    await db_session.flush()

    open_trades = await repo.get_open_trades(instance_id="test_bot")
    assert len(open_trades) == 1
    assert open_trades[0].symbol == "XAUUSD"


@pytest.mark.asyncio
async def test_trade_repo_closed_trades(db_session):
    repo = TradeRepository(db_session)
    await repo.create(symbol="XAUUSD", direction="BUY", outcome="WIN", instance_id="t1")
    await repo.create(symbol="XAUUSD", direction="SELL", outcome="LOSS", instance_id="t1")
    await repo.create(symbol="XAUUSD", direction="BUY", outcome="OPEN", instance_id="t1")
    await db_session.flush()

    closed = await repo.get_closed_trades(instance_id="t1")
    assert len(closed) == 2

    counts = await repo.count_by_outcome(instance_id="t1")
    assert counts.get("WIN") == 1
    assert counts.get("LOSS") == 1
    assert counts.get("OPEN") == 1
