"""Startup and shutdown hooks for the application."""

import logging

from alphaloop.core.container import Container

logger = logging.getLogger(__name__)


async def startup(container: Container) -> None:
    """Initialize all application resources."""
    logger.info("Starting AlphaLoop v3...")

    # Initialize database
    await container.init_db()
    logger.info("Database initialized")

    # Create tables if needed (dev mode)
    if container.config.db.url.startswith("sqlite"):
        from alphaloop.db.models import Base
        async with container.db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created")

    # Seed signal defaults into DB (skips keys that already have a value)
    from alphaloop.config.settings_service import SettingsService, SETTING_DEFAULTS
    settings_svc = SettingsService(container.db_session_factory)
    await settings_svc.seed_defaults(SETTING_DEFAULTS)


async def shutdown(container: Container) -> None:
    """Cleanup all application resources."""
    logger.info("Shutting down AlphaLoop v3...")
    await container.close()
    logger.info("Shutdown complete")
