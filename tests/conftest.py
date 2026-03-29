"""Shared test fixtures for AlphaLoop v3."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from alphaloop.core.config import AppConfig, DBConfig
from alphaloop.core.container import Container
from alphaloop.core.events import EventBus
from alphaloop.db.models.base import Base


@pytest.fixture
def config() -> AppConfig:
    """Test config with in-memory SQLite."""
    return AppConfig(
        db=DBConfig(url="sqlite+aiosqlite://", echo=False),
        dry_run=True,
        environment="test",
    )


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest_asyncio.fixture
async def db_engine():
    """In-memory async SQLite engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """Async session bound to the in-memory test database."""
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def container(config):
    """Container with in-memory DB for integration tests."""
    c = Container(config)
    c.db_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with c.db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    c.db_session_factory = async_sessionmaker(
        bind=c.db_engine, class_=AsyncSession, expire_on_commit=False
    )
    yield c
    await c.close()
