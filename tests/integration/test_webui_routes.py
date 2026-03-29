"""Integration tests for all WebUI API routes.

Uses httpx.AsyncClient with ASGITransport against the real FastAPI app
backed by an in-memory SQLite database (via the shared ``container`` fixture).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from alphaloop.webui.app import create_webui_app


@pytest_asyncio.fixture
async def client(container):
    """Async HTTP client wired to the ASGI app with in-memory DB."""
    app = create_webui_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
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


# ── Strategies ───────────────────────────────────────────────────────────────


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
