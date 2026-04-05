"""Integration tests for all WebUI API routes.

Uses httpx.AsyncClient with ASGITransport against the real FastAPI app
backed by an in-memory SQLite database (via the shared ``container`` fixture).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from alphaloop.db.models.incident import IncidentRecord
from alphaloop.db.models.operational_event import OperationalEvent
from alphaloop.db.models.order import OrderRecord
from alphaloop.db.models.trade import TradeLog
from alphaloop.webui.app import create_webui_app


_TEST_AUTH_TOKEN = "test-auth-token-for-integration"


@pytest_asyncio.fixture
async def client(container, monkeypatch):
    """Async HTTP client wired to the ASGI app with in-memory DB.

    Sets AUTH_TOKEN so Phase 7E auth enforcement passes for sensitive endpoints.
    """
    monkeypatch.setenv("AUTH_TOKEN", _TEST_AUTH_TOKEN)
    app = create_webui_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_TEST_AUTH_TOKEN}"},
    ) as c:
        yield c


# ── Health ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] == "ok"


# ── Dashboard ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard(client):
    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "open_trades",
        "daily_pnl",
        "daily_trades",
        "daily_win_rate",
        "weekly_pnl",
        "total_pnl",
        "total_trades",
        "overall_win_rate",
    ):
        assert key in data, f"Missing key: {key}"


# ── Trades ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trades(client):
    resp = await client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert "trades" in data
    assert isinstance(data["trades"], list)


@pytest.mark.asyncio
async def test_execution_ledger_and_reconciliation_routes(client, container):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(OrderRecord(
            order_id="ord-1",
            client_order_id="cid-1",
            symbol="XAUUSD",
            direction="BUY",
            lots=0.2,
            status="APPROVED",
            requested_price=2310.5,
            instance_id="test-instance",
            transitions=[{"to_status": "PENDING"}, {"to_status": "APPROVED"}],
        ))
        session.add(OperationalEvent(
            category="reconciliation",
            event_type="startup_reconciliation_completed",
            severity="warning",
            symbol="XAUUSD",
            instance_id="test-instance",
            message="Startup reconciliation completed",
            payload={"issue_count": 1, "has_critical": False},
            created_at=now,
        ))
        await session.commit()

    orders_resp = await client.get("/api/execution/orders")
    assert orders_resp.status_code == 200
    orders_data = orders_resp.json()
    assert orders_data["count"] >= 1
    assert orders_data["orders"][0]["client_order_id"] == "cid-1"

    recon_resp = await client.get("/api/execution/reconcile")
    assert recon_resp.status_code == 200
    recon_data = recon_resp.json()
    assert recon_data["latest_report"]["event_type"] == "startup_reconciliation_completed"
    assert recon_data["unresolved_count"] >= 1


@pytest.mark.asyncio
async def test_portfolio_and_incident_routes(client, container):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(TradeLog(
            signal_id="sig-open",
            client_order_id="cid-open",
            symbol="XAUUSD",
            direction="BUY",
            setup_type="trend",
            entry_price=2300.0,
            entry_zone_low=2299.0,
            entry_zone_high=2301.0,
            stop_loss=2290.0,
            take_profit_1=2320.0,
            lot_size=0.3,
            risk_pct=1.0,
            risk_amount_usd=120.0,
            outcome="OPEN",
            opened_at=now,
            order_ticket=101,
            instance_id="test-instance",
            is_dry_run=False,
        ))
        session.add(TradeLog(
            signal_id="sig-closed",
            client_order_id="cid-closed",
            symbol="XAUUSD",
            direction="SELL",
            setup_type="reversal",
            entry_price=2310.0,
            stop_loss=2320.0,
            take_profit_1=2290.0,
            lot_size=0.2,
            risk_pct=1.0,
            risk_amount_usd=80.0,
            outcome="WIN",
            opened_at=now,
            closed_at=now,
            pnl_usd=75.0,
            order_ticket=102,
            instance_id="test-instance",
            is_dry_run=False,
        ))
        session.add(IncidentRecord(
            incident_type="reconciliation_block",
            status="OPEN",
            severity="critical",
            title="Reconciliation Block",
            details="Startup reconciliation found unresolved issues",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "startup"},
        ))
        await session.commit()

    portfolio_resp = await client.get("/api/risk/portfolio")
    assert portfolio_resp.status_code == 200
    portfolio = portfolio_resp.json()
    assert portfolio["gross_risk_usd"] == 120.0
    assert portfolio["open_positions"] == 1

    incidents_resp = await client.get("/api/controls/incidents")
    assert incidents_resp.status_code == 200
    incidents_data = incidents_resp.json()
    assert incidents_data["count"] >= 1
    incident_id = incidents_data["incidents"][0]["id"]

    ack_resp = await client.post(
        f"/api/controls/incidents/{incident_id}/ack",
        json={"operator": "tester", "note": "acknowledged in integration test"},
    )
    assert ack_resp.status_code == 200
    assert ack_resp.json()["incident"]["status"] == "ACKNOWLEDGED"


# ── Settings ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_settings(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "settings" in data


@pytest.mark.asyncio
async def test_put_settings(client):
    resp = await client.put(
        "/api/settings",
        json={"settings": {"TEST_KEY": "value"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert "TEST_KEY" in data.get("updated", [])


# ── Backtests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backtests(client):
    resp = await client.get("/api/backtests")
    assert resp.status_code == 200
    data = resp.json()
    assert "backtests" in data
    assert isinstance(data["backtests"], list)


@pytest.mark.asyncio
async def test_backtests_symbols(client):
    resp = await client.get("/api/backtests/symbols")
    assert resp.status_code == 200
    data = resp.json()
    assert "symbols" in data
    assert isinstance(data["symbols"], list)


@pytest.mark.asyncio
async def test_mt5_symbols(client, monkeypatch):
    class _FakeSymbol:
        def __init__(self, name, description, visible=True, selected=True, path="Market\\Forex"):
            self.name = name
            self.description = description
            self.visible = visible
            self.select = selected
            self.path = path

    class _FakeMT5:
        def initialize(self, **kwargs):
            self.kwargs = kwargs
            return True

        def last_error(self):
            return (0, "OK")

        def symbols_get(self):
            return [
                _FakeSymbol("XAUUSDm", "Gold"),
                _FakeSymbol("EURUSD", "Euro / US Dollar", visible=False),
            ]

        def shutdown(self):
            return None

    monkeypatch.setitem(sys.modules, "MetaTrader5", _FakeMT5())
    await client.put(
        "/api/settings",
        json={
            "settings": {
                "MT5_SERVER": "demo-server",
                "MT5_LOGIN": "123456",
                "MT5_PASSWORD": "plain-password",
            }
        },
    )

    resp = await client.get("/api/test/mt5/symbols")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["source"] == "mt5"
    assert any(item["symbol"] == "XAUUSDm" for item in data["symbols"])


# ── Strategies ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_signal_discovery_mode_split(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Discovery Card",
            "signal_instruction": "Find strong momentum continuation setups.",
            "validator_instruction": "Reject weak setups.",
            "source": "ai_signal_discovery",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]
    assert created["signal_mode"] == "ai_signal"
    assert created["source"] == "ai_signal_discovery"

    blocked_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        json={
            "signal_mode": "ai_signal",
            "source": "strategies",
        },
    )
    assert blocked_resp.status_code == 400

    allowed_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        json={
            "signal_mode": "ai_signal",
            "source": "ai_signal_discovery",
            "name": "Discovery Card Updated",
        },
    )
    assert allowed_resp.status_code == 200
    updated = allowed_resp.json()["strategy"]
    assert updated["signal_mode"] == "ai_signal"
    assert updated["source"] == "ai_signal_discovery"

    list_resp = await client.get("/api/strategies?signal_mode=algo_only,algo_ai")
    assert list_resp.status_code == 200
    assert all(item.get("signal_mode") != "ai_signal" for item in list_resp.json()["strategies"])


@pytest.mark.asyncio
async def test_ai_signal_card_source_override(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Strategy Menu Card",
            "signal_instruction": "Prefer high-conviction trend continuation only.",
            "validator_instruction": "Reject marginal setups.",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]
    assert created["signal_mode"] == "ai_signal"
    assert created["source"] == "ui_ai_signal_card"


@pytest.mark.asyncio
async def test_strategies(client):
    resp = await client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert "strategies" in data
    assert isinstance(data["strategies"], list)


# ── Bots ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bots(client):
    resp = await client.get("/api/bots")
    assert resp.status_code == 200
    data = resp.json()
    assert "bots" in data
    assert isinstance(data["bots"], list)


# ── Tools (Pipeline) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tools(client):
    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "decisions" in data
    assert isinstance(data["decisions"], list)


# ── AI Hub ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_hub(client):
    resp = await client.get("/api/ai-hub")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "providers" in data
    assert isinstance(data["providers"], list)


# ── Research ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_research(client):
    resp = await client.get("/api/research")
    assert resp.status_code == 200
    data = resp.json()
    assert "reports" in data
    assert isinstance(data["reports"], list)
