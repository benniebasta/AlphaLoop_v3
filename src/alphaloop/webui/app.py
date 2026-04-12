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

        # Ensure any missing columns are added to existing tables
        from alphaloop.core.lifecycle import migrate_missing_columns
        await migrate_missing_columns(container.db_engine)

        # Phase 7B: Only remove instances where PID is no longer alive
        # (previously deleted ALL rows, hiding running bots after webUI restart)
        import os as _os_app
        import sys as _sys_app
        from sqlalchemy import select as _sa_select, delete as sa_delete
        from alphaloop.db.models.instance import RunningInstance
        import logging as _log

        def _pid_is_alive(pid: int) -> bool:
            """Check if a process is alive (cross-platform)."""
            if pid <= 0:
                return False
            try:
                if _sys_app.platform == "win32":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
                    if handle:
                        kernel32.CloseHandle(handle)
                        return True
                    return False
                else:
                    _os_app.kill(pid, 0)
                    return True
            except (OSError, PermissionError):
                return False

        async with container.db_session_factory() as _startup_session:
            result = await _startup_session.execute(_sa_select(RunningInstance))
            _cleaned = 0
            for inst in result.scalars():
                if not _pid_is_alive(inst.pid):
                    await _startup_session.delete(inst)
                    _cleaned += 1
            await _startup_session.commit()
            if _cleaned:
                _log.getLogger(__name__).info(
                    "Cleared %d stale running_instance(s) (dead PIDs only)",
                    _cleaned,
                )

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

        # Start dead-man's-switch as independent background task
        from alphaloop.monitoring.dead_man_switch import DeadManSwitch
        dms = DeadManSwitch(
            event_bus=container.event_bus,
            session_factory=container.db_session_factory,
        )
        app.state.dead_man_switch = dms
        dms_task = asyncio.create_task(dms.start())

        # Start weekly correlation matrix update background task
        async def _weekly_correlation_update() -> None:
            """Recompute correlation matrix from yfinance every 7 days."""
            import asyncio as _asyncio
            from alphaloop.tools.plugins.correlation_guard.updater import CorrelationMatrixUpdater
            from alphaloop.config.settings_service import SettingsService

            _corr_settings = SettingsService(container.db_session_factory)
            _corr_updater = CorrelationMatrixUpdater()

            while True:
                try:
                    ok = await _corr_updater.update_and_persist(_corr_settings)
                    if ok:
                        import logging as _log
                        _log.getLogger(__name__).info(
                            "Weekly correlation matrix updated successfully"
                        )
                except Exception as _ce:
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        "Weekly correlation update failed (non-critical): %s", _ce
                    )
                await _asyncio.sleep(7 * 24 * 3600)  # 7 days

        corr_task = asyncio.create_task(_weekly_correlation_update())

        yield

        watchdog.stop()
        watchdog_task.cancel()
        await dms.stop()
        dms_task.cancel()
        corr_task.cancel()
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

    # ── Security headers middleware ──────────────────────────────────────────
    from starlette.middleware.base import BaseHTTPMiddleware

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn-uicons.flaticon.com; "
                "img-src 'self' data:; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self' https://fonts.gstatic.com https://cdn-uicons.flaticon.com"
            )
            return response

    app.add_middleware(SecurityHeadersMiddleware)

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

    @app.get("/metrics")
    async def metrics():
        """Prometheus-compatible metrics endpoint."""
        from fastapi.responses import PlainTextResponse
        from alphaloop.monitoring.metrics import metrics_tracker
        return PlainTextResponse(
            metrics_tracker.get_prometheus_text(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

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
    from alphaloop.webui.routes.controls import router as controls_router
    from alphaloop.webui.routes.pipeline import router as pipeline_router
    from alphaloop.webui.routes.test_flow import router as test_flow_router

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
    app.include_router(controls_router)
    app.include_router(pipeline_router)
    app.include_router(test_flow_router)

    from alphaloop.webui.routes.alerts import router as alerts_router
    app.include_router(alerts_router)

    from alphaloop.webui.routes.assets import router as assets_router
    app.include_router(assets_router)

    from alphaloop.webui.routes.execution import router as execution_router
    app.include_router(execution_router)

    from alphaloop.webui.auth import auth_router
    app.include_router(auth_router)

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
