"""Async repository for TradeLog CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.trade import TradeLog, TradeAuditLog


class TradeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> TradeLog:
        trade = TradeLog(**kwargs)
        self._session.add(trade)
        await self._session.flush()
        return trade

    async def get_by_id(self, trade_id: int) -> TradeLog | None:
        result = await self._session.execute(
            select(TradeLog).where(TradeLog.id == trade_id)
        )
        return result.scalar_one_or_none()

    async def get_open_trades(self, instance_id: str | None = None) -> list[TradeLog]:
        q = select(TradeLog).where(TradeLog.outcome == "OPEN")
        if instance_id:
            q = q.where(TradeLog.instance_id == instance_id)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def get_closed_trades(
        self,
        symbol: str | None = None,
        strategy_version: str | None = None,
        instance_id: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[TradeLog]:
        q = select(TradeLog).where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
        if symbol:
            q = q.where(TradeLog.symbol == symbol)
        if strategy_version:
            q = q.where(TradeLog.strategy_version == strategy_version)
        if instance_id:
            q = q.where(TradeLog.instance_id == instance_id)
        if since:
            q = q.where(TradeLog.opened_at >= since)
        q = q.order_by(TradeLog.opened_at.desc()).limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def count_by_outcome(
        self, instance_id: str | None = None
    ) -> dict[str, int]:
        q = select(TradeLog.outcome, func.count()).group_by(TradeLog.outcome)
        if instance_id:
            q = q.where(TradeLog.instance_id == instance_id)
        result = await self._session.execute(q)
        return {row[0] or "unknown": row[1] for row in result}

    async def add_audit_entry(
        self,
        trade_id: int,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        changed_by: str = "system",
    ) -> None:
        entry = TradeAuditLog(
            trade_id=trade_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )
        self._session.add(entry)
