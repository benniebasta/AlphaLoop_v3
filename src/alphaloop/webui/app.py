"""
FastAPI application factory.

Creates the web server with health endpoint, API routes, and static file serving.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from alphaloop import __version__
from alphaloop.core.config import AppConfig
from alphaloop.core.container import Container


_STATIC_DIR = Path(__file__).parent / "static"


def create_webui_app(container: Container) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await container.init_db()

        # Start watchdog as background task
        from alphaloop.monitoring.watchdog import TradingWatchdog
        from alphaloop.monitoring.health import HealthCheck
        health = HealthCheck()
        watchdog = TradingWatchdog(
            health_check=health,
            event_bus=container.event_bus,
        )
        app.state.health_check = health
        app.state.watchdog = watchdog
        watchdog_task = asyncio.create_task(watchdog.run())

        yield

        watchdog.stop()
        watchdog_task.cancel()
        await container.close()

    app = FastAPI(
        title="AlphaLoop\u2122",
        version=__version__,
        lifespan=lifespan,
    )

    # Store container for dependency injection in routes
    app.state.container = container

    # Set app ref for background task access to session factory
    from alphaloop.webui import deps as _deps
    _deps._app_ref = app

    # ── Auth middleware ───────────────────────────────────────────────────────
    from alphaloop.webui.auth import BearerAuthMiddleware

    app.add_middleware(BearerAuthMiddleware)

    # ── Health endpoints ─────────────────────────────────────────────────────
    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "version": __version__,
        })

    @app.get("/health/detailed")
    async def health_detailed() -> JSONResponse:
        """Detailed health report with all component statuses."""
        report = {"status": "ok", "version": __version__, "components": {}}
        if hasattr(app.state, "health_check"):
            report = app.state.health_check.get_report()
            report["version"] = __version__
        if hasattr(app.state, "watchdog"):
            report["watchdog"] = app.state.watchdog.get_status()
        return JSONResponse(report)

    # ── API routes ───────────────────────────────────────────────────────────
    from alphaloop.webui.routes.dashboard import router as dashboard_router
    from alphaloop.webui.routes.trades import router as trades_router
    from alphaloop.webui.routes.settings import router as settings_router
    from alphaloop.webui.routes.bots import router as bots_router
    from alphaloop.webui.routes.backtests import router as backtests_router
    from alphaloop.webui.routes.tools import router as tools_router
    from alphaloop.webui.routes.ai_hub import router as ai_hub_router
    from alphaloop.webui.routes.research import router as research_router
    from alphaloop.webui.routes.seedlab import router as seedlab_router
    from alphaloop.webui.routes.strategies import router as strategies_router
    from alphaloop.webui.routes.websocket import router as ws_router
    from alphaloop.webui.routes.test_connections import router as test_router
    from alphaloop.webui.routes.live import router as live_router
    from alphaloop.webui.routes.event_log import router as event_log_router
    from alphaloop.webui.routes.risk_dashboard import router as risk_dashboard_router

    app.include_router(dashboard_router)
    app.include_router(trades_router)
    app.include_router(settings_router)
    app.include_router(bots_router)
    app.include_router(backtests_router)
    app.include_router(tools_router)
    app.include_router(ai_hub_router)
    app.include_router(research_router)
    app.include_router(seedlab_router)
    app.include_router(strategies_router)
    app.include_router(ws_router)
    app.include_router(test_router)
    app.include_router(live_router)
    app.include_router(event_log_router)
    app.include_router(risk_dashboard_router)

    # ── Static files ─────────────────────────────────────────────────────────
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── Root redirect to index.html ──────────────────────────────────────────
    @app.get("/")
    async def root():
        from fastapi.responses import FileResponse

        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return JSONResponse({"message": "AlphaLoop WebUI", "version": __version__})

    return app


def run_server(config: AppConfig | None = None) -> None:
    """Start the uvicorn server — convenience entry point."""
    import uvicorn

    from alphaloop.core.constants import WEBUI_DEFAULT_PORT

    if config is None:
        config = AppConfig()

    container = Container(config)
    app = create_webui_app(container)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=WEBUI_DEFAULT_PORT,
        log_level=config.log_level.lower(),
    )
