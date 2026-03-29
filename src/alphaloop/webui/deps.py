"""FastAPI dependency injection helpers."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.core.config import AppConfig
from alphaloop.core.container import Container


_app_ref = None  # Set by app.py after creating the FastAPI app


def get_container(request: Request) -> Container:
    """Retrieve the DI container stored on app state."""
    return request.app.state.container


def _get_session_factory():
    """Get session factory from app state (for background tasks)."""
    if _app_ref and hasattr(_app_ref.state, "container"):
        return _app_ref.state.container.db_session_factory
    return None


def get_config(container: Container = Depends(get_container)) -> AppConfig:
    """Retrieve application configuration."""
    return container.config


async def get_db_session(
    container: Container = Depends(get_container),
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session, auto-committed on success."""
    if container.db_session_factory is None:
        raise RuntimeError("Database not initialised")
    async with container.db_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
