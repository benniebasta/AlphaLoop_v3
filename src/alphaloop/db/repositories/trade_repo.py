"""Async repository for TradeLog CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from datetime import timedelta, timezone
from sqlalchemy import select, update, func
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

    async def get_open_trades(
        self,
        instance_id: str | None = None,
        symbol: str | None = None,
    ) -> list[TradeLog]:
        q = select(TradeLog).where(TradeLog.outcome == "OPEN")
        if instance_id:
            q = q.where(TradeLog.instance_id == instance_id)
        if symbol:
            q = q.where(TradeLog.symbol == symbol)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def get_closed_trades(
        self,
        symbol: str | None = None,
        strategy_version: str | None = None,
        instance_id: str | None = None,
        since: datetime | None = None,
        closed_since: datetime | None = None,
        limit: int | None = 500,
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
        if closed_since:
            q = q.where(TradeLog.closed_at >= closed_since)
        q = q.order_by(TradeLog.closed_at.desc(), TradeLog.opened_at.desc())
        if limit is not None:
            q = q.limit(limit)
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

    async def update_attribution(
        self,
        trade_id: int,
        attribution: dict[str, float | None],
    ) -> None:
        """Update P&L attribution columns on a closed trade."""
        # Only update fields that are present in attribution dict
        allowed = {"pnl_entry_skill", "pnl_exit_skill", "pnl_slippage_usd", "pnl_commission_usd"}
        values = {k: v for k, v in attribution.items() if k in allowed and v is not None}
        if not values:
            return
        await self._session.execute(
            update(TradeLog).where(TradeLog.id == trade_id).values(**values)
        )

    async def get_pending_trades(
        self,
        instance_id: str | None = None,
        older_than_minutes: int = 5,
    ) -> list[TradeLog]:
        """Return PENDING trades that may represent orphaned broker positions.

        PENDING = broker call was made but DB confirm write was interrupted.
        Trades older than `older_than_minutes` should be reconciled against broker.
        """
        from datetime import datetime
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        q = (
            select(TradeLog)
            .where(TradeLog.outcome == "PENDING")
            .where(TradeLog.opened_at < cutoff)
        )
        if instance_id:
            q = q.where(TradeLog.instance_id == instance_id)
        result = await self._session.execute(q)
        return list(result.scalars())

    # ── Phase 2B: Lifecycle methods ────────────────────────────────────────

    async def get_by_ticket(self, ticket: int) -> TradeLog | None:
        """Look up a trade by broker order ticket."""
        result = await self._session.execute(
            select(TradeLog).where(TradeLog.order_ticket == ticket)
        )
        return result.scalar_one_or_none()

    async def close_trade(
        self,
        trade_id: int,
        close_price: float,
        pnl_usd: float,
        outcome: str,
        *,
        changed_by: str = "system",
    ) -> None:
        """Close an OPEN trade — updates outcome/price and creates audit entries.

        Raises ValueError if the trade is not OPEN.
        """
        trade = await self.get_by_id(trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")
        if trade.outcome != "OPEN":
            raise ValueError(
                f"Trade {trade_id} is not OPEN (current outcome={trade.outcome})"
            )

        from datetime import datetime, timezone
        old_outcome = trade.outcome
        trade.outcome = outcome
        trade.close_price = close_price
        trade.pnl_usd = pnl_usd
        trade.closed_at = datetime.now(timezone.utc)
        await self._session.flush()

        # Audit trail
        await self.add_audit_entry(trade_id, "outcome", old_outcome, outcome, changed_by)
        await self.add_audit_entry(trade_id, "close_price", None, str(close_price), changed_by)
        await self.add_audit_entry(trade_id, "pnl_usd", None, str(pnl_usd), changed_by)

    async def update_trade(
        self,
        trade_id: int,
        *,
        changed_by: str = "system",
        **fields: Any,
    ) -> None:
        """Generic field update with audit trail for each changed field."""
        trade = await self.get_by_id(trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")

        for field_name, new_value in fields.items():
            if not hasattr(trade, field_name):
                continue
            old_value = getattr(trade, field_name)
            setattr(trade, field_name, new_value)
            await self.add_audit_entry(
                trade_id, field_name,
                str(old_value) if old_value is not None else None,
                str(new_value) if new_value is not None else None,
                changed_by,
            )
        await self._session.flush()

    # ─────────────────────────────────────────────────────────────────────────

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
        await self._session.flush()
