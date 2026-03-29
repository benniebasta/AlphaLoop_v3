"""
Dependency injection container.

Creates and wires all application components. Replaces module-level singletons.
Tests can create alternate containers with mock dependencies.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from alphaloop.core.config import AppConfig
from alphaloop.core.events import EventBus


class Container:
    """
    Central DI container — holds all shared application state.

    Constructed once in app.py, then passed to components that need dependencies.
    Components receive what they need via constructor injection, never by
    importing globals.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.event_bus = EventBus()

        # Populated during startup (lifecycle.py)
        self.db_engine: AsyncEngine | None = None
        self._db_session_factory: async_sessionmaker | None = None

    @property
    def db_session_factory(self) -> async_sessionmaker:
        """Get session factory, raising if DB not initialized."""
        if self._db_session_factory is None:
            raise RuntimeError("Database not initialized. Call init_db() first.")
        return self._db_session_factory

    @db_session_factory.setter
    def db_session_factory(self, value: async_sessionmaker | None) -> None:
        self._db_session_factory = value

    async def init_db(self) -> None:
        """Initialize the async database engine and session factory."""
        from alphaloop.db.engine import create_db_engine
        from alphaloop.db.session import create_session_factory

        self.db_engine = create_db_engine(self.config.db)
        self.db_session_factory = create_session_factory(self.db_engine)

    async def close(self) -> None:
        """Cleanup resources on shutdown."""
        if self.db_engine:
            await self.db_engine.dispose()
