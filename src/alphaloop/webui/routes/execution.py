"""
Execution APIs backed by durable execution and reconciliation records.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.operational_event import OperationalEvent
from alphaloop.db.models.order import OrderRecord
from alphaloop.db.models.trade import TradeLog
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/execution", tags=["execution"])


def _serialize_order(order: OrderRecord) -> dict:
    return {
        "order_id": order.order_id,
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "direction": order.direction,
        "lots": order.lots,
        "status": order.status,
        "broker_ticket": order.broker_ticket,
        "requested_price": order.requested_price,
        "fill_price": order.fill_price,
        "fill_volume": order.fill_volume,
        "slippage_points": order.slippage_points,
        "spread_at_fill": order.spread_at_fill,
        "error_message": order.error_message,
        "instance_id": order.instance_id,
        "transitions": order.transitions or [],
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "updated_at": order.updated_at.isoformat() if order.updated_at else None,
    }


def _serialize_event(event: OperationalEvent) -> dict:
    return {
        "id": event.id,
        "category": event.category,
        "event_type": event.event_type,
        "severity": event.severity,
        "symbol": event.symbol,
        "instance_id": event.instance_id,
        "entity_id": event.entity_id,
        "message": event.message,
        "payload": event.payload or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/orders")
async def list_execution_orders(
    limit: int = 200,
    unresolved_only: bool = False,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    query = select(OrderRecord).order_by(OrderRecord.created_at.desc()).limit(limit)
    if unresolved_only:
        query = (
            select(OrderRecord)
            .where(OrderRecord.status.in_(["PENDING", "APPROVED", "SENT", "PARTIAL", "RECOVERY_PENDING"]))
            .order_by(OrderRecord.created_at.desc())
            .limit(limit)
        )
    orders = list((await session.execute(query)).scalars())
    unresolved = sum(
        1 for order in orders
        if order.status in {"PENDING", "APPROVED", "SENT", "PARTIAL", "RECOVERY_PENDING"}
    )
    return {
        "orders": [_serialize_order(order) for order in orders],
        "count": len(orders),
        "unresolved_count": unresolved,
    }


@router.get("/reconcile")
async def get_reconciliation_status(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    latest_q = (
        select(OperationalEvent)
        .where(OperationalEvent.category == "reconciliation")
        .order_by(OperationalEvent.created_at.desc())
        .limit(1)
    )
    latest = (await session.execute(latest_q)).scalar_one_or_none()

    unresolved_q = select(OrderRecord).where(
        OrderRecord.status.in_(["PENDING", "APPROVED", "SENT", "PARTIAL", "RECOVERY_PENDING"])
    )
    unresolved_orders = list((await session.execute(unresolved_q)).scalars())

    return {
        "latest_report": _serialize_event(latest) if latest else None,
        "unresolved_orders": [_serialize_order(order) for order in unresolved_orders],
        "unresolved_count": len(unresolved_orders),
    }


@router.get("/tca")
async def get_tca(
    limit: int = 200,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Compute Transaction Cost Analysis metrics from closed trade history.

    Returns execution quality score (0–100), avg slippage, spread costs.
    """
    try:
        from alphaloop.execution.tca import TCAAnalyzer

        q = (
            select(TradeLog)
            .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
            .order_by(TradeLog.closed_at.desc())
            .limit(limit)
        )
        rows = list((await session.execute(q)).scalars())

        trades = [
            {
                "slippage_points": t.slippage_points,
                "execution_spread": t.execution_spread,
                "lot_size": t.lot_size,
                "pnl_usd": t.pnl_usd,
                "atr_h1": getattr(t, "atr_h1", None),
            }
            for t in rows
        ]

        analyzer = TCAAnalyzer(trades)
        result = analyzer.compute()
        result["analyzed_trades"] = limit
        return result

    except Exception as e:
        return {
            "error": str(e),
            "trade_count": 0,
            "execution_quality_score": None,
        }


@router.post("/attribution/backfill")
async def backfill_attribution(
    limit: int = 500,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Retroactively compute and save P&L attribution for closed trades
    that don't have it yet (pnl_entry_skill IS NULL).

    Useful after upgrading to v3.1 — backfills historical data.
    """
    try:
        from alphaloop.research.attribution import TradeAttributor
        from alphaloop.db.repositories.trade_repo import TradeRepository

        q = (
            select(TradeLog)
            .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
            .where(TradeLog.pnl_entry_skill.is_(None))
            .order_by(TradeLog.closed_at.desc())
            .limit(limit)
        )
        rows = list((await session.execute(q)).scalars())

        attributor = TradeAttributor()
        repo = TradeRepository(session)
        updated = 0
        skipped = 0

        for trade in rows:
            trade_dict = {
                "entry_price": trade.entry_price,
                "close_price": trade.close_price,
                "lot_size": trade.lot_size,
                "direction": trade.direction,
                "entry_zone_low": trade.entry_zone_low,
                "entry_zone_high": trade.entry_zone_high,
                "stop_loss": trade.stop_loss,
                "take_profit_1": trade.take_profit_1,
                "slippage_points": trade.slippage_points,
                "execution_spread": trade.execution_spread,
            }
            attrs = attributor.compute_attribution(trade_dict)
            if any(v is not None for v in attrs.values()):
                await repo.update_attribution(trade.id, attrs)
                updated += 1
            else:
                skipped += 1

        await session.commit()
        return {"status": "ok", "updated": updated, "skipped_insufficient_data": skipped}

    except Exception as e:
        return {"status": "error", "error": str(e), "updated": 0}
