"""Test the health endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from alphaloop.core.config import AppConfig, DBConfig
from alphaloop.core.container import Container
from alphaloop.webui.app import create_webui_app


@pytest.mark.asyncio
async def test_health_endpoint():
    config = AppConfig(db=DBConfig(url="sqlite+aiosqlite://"))
    container = Container(config)
    try:
        app = create_webui_app(container)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
    finally:
        await container.close()
