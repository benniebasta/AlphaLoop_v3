"""Async repository for strategy version management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.strategy import StrategyVersion


class StrategyRepository:
    _UPDATABLE_FIELDS = frozenset({
        "status", "params_json", "fitness", "notes", "error",
        "canary_id", "canary_start", "canary_end", "canary_result",
        "promoted_at", "updated_at",
    })

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> StrategyVersion:
        v = StrategyVersion(**kwargs)
        self._session.add(v)
        await self._session.flush()
        return v

    async def get_by_symbol_version(
        self, symbol: str, version: int
    ) -> StrategyVersion | None:
        result = await self._session.execute(
            select(StrategyVersion)
            .where(StrategyVersion.symbol == symbol, StrategyVersion.version == version)
        )
        return result.scalar_one_or_none()

    async def get_active(self, symbol: str) -> StrategyVersion | None:
        """Get the active (live or demo) strategy for a symbol."""
        result = await self._session.execute(
            select(StrategyVersion)
            .where(
                StrategyVersion.symbol == symbol,
                StrategyVersion.status.in_(["live", "demo"]),
            )
            .order_by(StrategyVersion.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_versions(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[StrategyVersion]:
        q = select(StrategyVersion)
        if symbol:
            q = q.where(StrategyVersion.symbol == symbol)
        if status:
            q = q.where(StrategyVersion.status == status)
        q = q.order_by(StrategyVersion.created_at.desc()).limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def update_status(
        self,
        symbol: str,
        version: int,
        new_status: str,
        **kwargs: Any,
    ) -> StrategyVersion | None:
        v = await self.get_by_symbol_version(symbol, version)
        if v is None:
            return None
        v.status = new_status
        v.updated_at = datetime.now(timezone.utc)
        for key, value in kwargs.items():
            if key in self._UPDATABLE_FIELDS and hasattr(v, key):
                setattr(v, key, value)
        return v

    async def set_canary(
        self,
        symbol: str,
        version: int,
        canary_id: str,
        start: datetime,
        end: datetime,
    ) -> StrategyVersion | None:
        v = await self.get_by_symbol_version(symbol, version)
        if v is None:
            return None
        v.canary_id = canary_id
        v.canary_start = start
        v.canary_end = end
        v.canary_result = "running"
        return v

    async def end_canary(
        self,
        symbol: str,
        version: int,
        result: str,
    ) -> StrategyVersion | None:
        v = await self.get_by_symbol_version(symbol, version)
        if v is None:
            return None
        v.canary_end = datetime.now(timezone.utc)
        v.canary_result = result
        return v
