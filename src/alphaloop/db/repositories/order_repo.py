"""Async repository for OrderRecord CRUD operations — Phase 2C."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.order import OrderRecord

# Non-terminal states: order may still result in a fill or need resolution
_NON_TERMINAL = {"PENDING", "APPROVED", "SENT", "PARTIAL", "RECOVERY_PENDING"}


def _transition_entry(
    from_status: str | None,
    to_status: str,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "from": from_status,
        "to": to_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": fields or {},
    }


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        order_id: str,
        symbol: str,
        direction: str,
        lots: float,
        *,
        instance_id: str | None = None,
        client_order_id: str | None = None,
        requested_price: float | None = None,
    ) -> OrderRecord:
        """Persist a new PENDING order intent before broker submission."""
        record = OrderRecord(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            lots=lots,
            status="PENDING",
            instance_id=instance_id,
            client_order_id=client_order_id,
            requested_price=requested_price,
            transitions=[_transition_entry(None, "PENDING", {"requested_price": requested_price})],
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def update_status(
        self,
        order_id: str,
        status: str,
        **fields: Any,
    ) -> None:
        """Update order status and any additional fields."""
        record = await self.get_by_order_id(order_id)
        if record is None:
            raise ValueError(f"OrderRecord not found: {order_id}")
        old_status = record.status
        record.status = status
        record.updated_at = datetime.now(timezone.utc)
        for k, v in fields.items():
            if hasattr(record, k):
                setattr(record, k, v)
        transitions = list(record.transitions or [])
        transitions.append(_transition_entry(old_status, status, fields))
        record.transitions = transitions
        await self._session.flush()

    async def get_by_order_id(self, order_id: str) -> OrderRecord | None:
        result = await self._session.execute(
            select(OrderRecord).where(OrderRecord.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_ticket(self, ticket: int) -> OrderRecord | None:
        result = await self._session.execute(
            select(OrderRecord).where(OrderRecord.broker_ticket == ticket)
        )
        return result.scalar_one_or_none()

    async def get_non_terminal(self) -> list[OrderRecord]:
        """All orders in non-terminal states — for startup recovery."""
        result = await self._session.execute(
            select(OrderRecord).where(OrderRecord.status.in_(_NON_TERMINAL))
        )
        return list(result.scalars())

    async def list_orders(
        self,
        *,
        limit: int = 200,
        unresolved_only: bool = False,
    ) -> list[OrderRecord]:
        query = select(OrderRecord).order_by(OrderRecord.created_at.desc()).limit(limit)
        if unresolved_only:
            query = (
                select(OrderRecord)
                .where(OrderRecord.status.in_(_NON_TERMINAL))
                .order_by(OrderRecord.created_at.desc())
                .limit(limit)
            )
        result = await self._session.execute(query)
        return list(result.scalars())

    async def get_by_client_id(self, client_order_id: str) -> OrderRecord | None:
        """Phase 5: look up by deterministic client order ID."""
        result = await self._session.execute(
            select(OrderRecord).where(OrderRecord.client_order_id == client_order_id)
        )
        return result.scalar_one_or_none()
