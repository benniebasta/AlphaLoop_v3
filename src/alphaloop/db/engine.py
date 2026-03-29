"""
Async database engine factory.

Supports SQLite (dev, via aiosqlite) and PostgreSQL (prod, via asyncpg).
Applies dialect-specific settings automatically.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alphaloop.core.config import DBConfig

logger = logging.getLogger(__name__)


def _apply_sqlite_pragmas(dbapi_conn: Any, connection_record: Any) -> None:
    """Enable WAL mode and FULL synchronous on SQLite connections."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=FULL")
    cursor.close()


def _is_sqlite(url: str) -> bool:
    return "sqlite" in url


def create_db_engine(db_config: DBConfig) -> AsyncEngine:
    """
    Create an async SQLAlchemy engine from config.

    Dialect-aware:
    - SQLite: WAL pragma, 30s timeout, no pool_size
    - PostgreSQL: connection pool with configured pool_size
    """
    url = db_config.url
    kwargs: dict[str, Any] = {"echo": db_config.echo}

    if _is_sqlite(url):
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs["pool_size"] = db_config.pool_size

    engine = create_async_engine(url, **kwargs)

    if _is_sqlite(url):
        event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)

    safe_url = re.sub(r'://[^@]+@', '://***@', url.split('?')[0])
    logger.info(f"[db] Created async engine: {safe_url}")
    return engine
