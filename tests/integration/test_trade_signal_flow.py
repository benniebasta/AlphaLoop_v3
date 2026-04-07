"""Integration tests: signal entry/close DB write and API display.

Covers:
  1. ExecutionService.execute_market_order() writes OPEN TradeLog to DB
  2. TradeRepository.close_trade() updates to WIN + creates audit entries
  3. GET /api/trades?status=open  — open trade visible
  4. GET /api/trades?status=closed — closed trade visible
  5. GET /api/dashboard            — open_trades count correct
  6. GET /api/dashboard            — daily_pnl reflects closed WIN trade
  7. GET /api/live                 — recent_trades contains closed trade
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import alphaloop.webui.routes.live as live_route
import alphaloop.webui.routes.strategies as strategies_route

from alphaloop.db.models.trade import TradeLog, TradeAuditLog
from alphaloop.db.repositories.trade_repo import TradeRepository
from alphaloop.execution.schemas import OrderResult
from alphaloop.execution.service import ExecutionService
from alphaloop.webui.app import create_webui_app

# ---------------------------------------------------------------------------
# Shared constants / helpers
# ---------------------------------------------------------------------------

_TEST_AUTH_TOKEN = "test-signal-flow-token"


def _fake_ohlc_tuple():
    """12-element tuple that _fetch_ohlc normally returns — all nulls/empty."""
    return ([], None, None, None, None, "calm", None, None, None, "ranging", [], None)


def _make_signal(direction: str = "BUY") -> SimpleNamespace:
    return SimpleNamespace(
        direction=direction,
        entry_zone=(3100.0, 3104.0),
        setup_type="pullback",
        rr_ratio=1.5,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(container, monkeypatch, tmp_path):
    """AsyncClient wired to the ASGI app with an in-memory DB."""
    monkeypatch.setenv("AUTH_TOKEN", _TEST_AUTH_TOKEN)
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", tmp_path)
    app = create_webui_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_TEST_AUTH_TOKEN}"},
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Test 1 — Trade entry → DB write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_entry_writes_open_trade_to_db(container):
    """ExecutionService writes a OPEN TradeLog with correct fields after broker fill."""
    executor = SimpleNamespace(
        open_order=AsyncMock(
            return_value=OrderResult(
                success=True,
                order_ticket=99001,
                fill_price=3101.0,
                fill_volume=0.1,
            )
        )
    )
    control_plane = SimpleNamespace(
        preflight=AsyncMock(
            return_value=SimpleNamespace(
                approved=True,
                reason="",
                order_id="ord-t1",
                client_order_id="cid-t1",
                projected_risk_usd=150.0,
            )
        )
    )

    svc = ExecutionService(
        session_factory=container.db_session_factory,
        executor=executor,
        control_plane=control_plane,
        supervision_service=None,
        dry_run=True,
    )
    # Bypass OrderRecord dependency — not what we're testing here
    svc._update_order_status = AsyncMock()

    report = await svc.execute_market_order(
        symbol="XAUUSD",
        instance_id="inst-test",
        account_balance=10_000.0,
        signal=_make_signal(),
        sizing={"lots": 0.1, "risk_pct": 1.0, "risk_amount_usd": 100.0},
        stop_loss=3090.0,
        take_profit=3115.0,
        take_profit_2=3125.0,
        strategy_version="v-test",
        is_dry_run=True,
    )

    assert report.status == "FILLED", f"Expected FILLED, got {report.status}"
    assert report.trade_id is not None

    async with container.db_session_factory() as session:
        trade = (
            await session.execute(
                select(TradeLog).where(TradeLog.id == report.trade_id)
            )
        ).scalar_one()

    assert trade.outcome == "OPEN"
    assert trade.direction == "BUY"
    assert trade.symbol == "XAUUSD"
    assert trade.entry_price == 3101.0   # fill_price from broker, set by _confirm_trade_log
    assert trade.stop_loss == 3090.0
    assert trade.take_profit_1 == 3115.0
    assert trade.take_profit_2 == 3125.0
    assert trade.lot_size == 0.1
    assert trade.order_ticket == 99001
    assert trade.is_dry_run is True


# ---------------------------------------------------------------------------
# Test 2 — Trade close → DB update + audit trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trade_close_updates_db_and_creates_audit(db_session):
    """close_trade() sets WIN outcome/price/pnl and writes 3 audit entries."""
    repo = TradeRepository(db_session)
    trade = await repo.create(
        symbol="XAUUSD",
        direction="BUY",
        outcome="OPEN",
        instance_id="test-inst",
        entry_price=3100.0,
        stop_loss=3090.0,
        take_profit_1=3115.0,
        lot_size=0.1,
    )
    await db_session.flush()
    trade_id = trade.id

    await repo.close_trade(
        trade_id,
        close_price=3115.0,
        pnl_usd=100.0,
        outcome="WIN",
        changed_by="test",
    )
    await db_session.commit()

    closed = await repo.get_by_id(trade_id)
    assert closed.outcome == "WIN"
    assert closed.close_price == 3115.0
    assert closed.pnl_usd == 100.0
    assert closed.closed_at is not None
    assert closed.closed_at.tzinfo is not None  # timezone-aware

    entries = list(
        (
            await db_session.execute(
                select(TradeAuditLog).where(TradeAuditLog.trade_id == trade_id)
            )
        ).scalars()
    )
    assert len(entries) == 3
    field_names = {e.field_name for e in entries}
    assert field_names == {"outcome", "close_price", "pnl_usd"}

    outcome_entry = next(e for e in entries if e.field_name == "outcome")
    assert outcome_entry.old_value == "OPEN"
    assert outcome_entry.new_value == "WIN"
    assert outcome_entry.changed_by == "test"


# ---------------------------------------------------------------------------
# Test 3 — /api/trades?status=open shows OPEN trade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trades_api_returns_open_trade(client, container):
    """GET /api/trades?status=open includes a seeded OPEN trade."""
    async with container.db_session_factory() as session:
        session.add(
            TradeLog(
                symbol="XAUUSD",
                direction="BUY",
                outcome="OPEN",
                entry_price=3100.0,
                stop_loss=3090.0,
                take_profit_1=3115.0,
                lot_size=0.1,
            )
        )
        await session.commit()

    resp = await client.get("/api/trades?status=open")
    assert resp.status_code == 200
    data = resp.json()
    assert "trades" in data
    assert len(data["trades"]) >= 1

    match = next(
        (t for t in data["trades"] if t["symbol"] == "XAUUSD" and t["outcome"] == "OPEN"),
        None,
    )
    assert match is not None, "OPEN XAUUSD trade not found in response"
    assert match["direction"] == "BUY"
    assert match["entry_price"] == 3100.0
    assert match["stop_loss"] == 3090.0
    assert match["take_profit_1"] == 3115.0


# ---------------------------------------------------------------------------
# Test 4 — /api/trades?status=closed shows WIN trade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trades_api_returns_closed_trade(client, container):
    """GET /api/trades?status=closed includes a seeded WIN trade."""
    async with container.db_session_factory() as session:
        session.add(
            TradeLog(
                symbol="XAUUSD",
                direction="BUY",
                outcome="WIN",
                entry_price=3100.0,
                close_price=3115.0,
                pnl_usd=100.0,
                closed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    resp = await client.get("/api/trades?status=closed")
    assert resp.status_code == 200
    data = resp.json()
    assert "trades" in data

    match = next(
        (t for t in data["trades"] if t["symbol"] == "XAUUSD" and t["outcome"] == "WIN"),
        None,
    )
    assert match is not None, "WIN XAUUSD trade not found in response"
    assert match["pnl_usd"] == 100.0
    assert match["close_price"] == 3115.0


# ---------------------------------------------------------------------------
# Test 5 — /api/dashboard open_trades count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_shows_open_trade_count(client, container):
    """GET /api/dashboard reflects the count of OPEN trades in DB."""
    async with container.db_session_factory() as session:
        session.add(TradeLog(symbol="XAUUSD", direction="BUY", outcome="OPEN", lot_size=0.1))
        session.add(TradeLog(symbol="XAUUSD", direction="SELL", outcome="OPEN", lot_size=0.05))
        await session.commit()

    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    assert data["open_trades"] == 2
    for key in ("daily_pnl", "daily_trades", "daily_win_rate", "weekly_pnl", "total_pnl", "total_trades", "overall_win_rate"):
        assert key in data, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Test 6 — /api/dashboard daily_pnl from WIN trade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_reflects_closed_trade_pnl(client, container):
    """GET /api/dashboard daily_pnl sums pnl_usd of trades closed today."""
    async with container.db_session_factory() as session:
        session.add(
            TradeLog(
                symbol="XAUUSD",
                direction="BUY",
                outcome="WIN",
                pnl_usd=100.0,
                closed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    assert data["daily_pnl"] == 100.0
    assert data["daily_trades"] == 1
    assert data["daily_win_rate"] == 100.0


# ---------------------------------------------------------------------------
# Test 7 — /api/live recent_trades shows closed trade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_api_shows_recent_closed_trade(client, container, monkeypatch):
    """GET /api/live recent_trades includes seeded WIN trade (yfinance stubbed)."""
    monkeypatch.setattr(live_route, "_fetch_ohlc", AsyncMock(return_value=_fake_ohlc_tuple()))
    monkeypatch.setattr(live_route, "_cache", {})

    async with container.db_session_factory() as session:
        session.add(
            TradeLog(
                symbol="XAUUSD",
                direction="BUY",
                outcome="WIN",
                pnl_usd=75.0,
                closed_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    resp = await client.get("/api/live?symbol=XAUUSD&timeframe=1m")
    assert resp.status_code == 200
    data = resp.json()

    assert "recent_trades" in data
    assert len(data["recent_trades"]) >= 1

    match = next(
        (t for t in data["recent_trades"] if t["outcome"] == "WIN"),
        None,
    )
    assert match is not None, "WIN trade not found in recent_trades"
    assert match["direction"] == "BUY"
    assert match["pnl"] == 75.0          # key is 'pnl', not 'pnl_usd' (live.py:381)
    assert match["closed_at"] is not None
