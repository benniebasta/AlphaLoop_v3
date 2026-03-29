"""CRUD /api/trades — list, get by id, trade history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.repositories.trade_repo import TradeRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _trade_to_dict(t) -> dict:
    """Serialise a TradeLog ORM object to a plain dict."""
    return {
        "id": t.id,
        "signal_id": t.signal_id,
        "symbol": t.symbol,
        "direction": t.direction,
        "setup_type": t.setup_type,
        "timeframe": t.timeframe,
        "entry_price": t.entry_price,
        "stop_loss": t.stop_loss,
        "take_profit_1": t.take_profit_1,
        "take_profit_2": t.take_profit_2,
        "lot_size": t.lot_size,
        "risk_pct": t.risk_pct,
        "risk_amount_usd": t.risk_amount_usd,
        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "session_name": t.session_name,
        "close_price": t.close_price,
        "pnl_usd": t.pnl_usd,
        "pnl_r": t.pnl_r,
        "outcome": t.outcome,
        "strategy_version": t.strategy_version,
        "instance_id": t.instance_id,
        "is_dry_run": t.is_dry_run,
        "qwen_confidence": t.qwen_confidence,
        "claude_risk_score": t.claude_risk_score,
        "rr_ratio": t.rr_ratio,
    }


@router.get("")
async def list_trades(
    status: str = Query("all", pattern="^(all|open|closed)$"),
    symbol: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """List trades filtered by status."""
    repo = TradeRepository(session)
    if status == "open":
        trades = await repo.get_open_trades()
    elif status == "closed":
        trades = await repo.get_closed_trades(symbol=symbol, limit=limit)
    else:
        open_trades = await repo.get_open_trades()
        closed_trades = await repo.get_closed_trades(symbol=symbol, limit=limit)
        trades = open_trades + closed_trades
    return {"trades": [_trade_to_dict(t) for t in trades]}


@router.get("/{trade_id}")
async def get_trade(
    trade_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get a single trade by ID."""
    repo = TradeRepository(session)
    trade = await repo.get_by_id(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _trade_to_dict(trade)


@router.get("/stats/summary")
async def trade_stats(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Outcome distribution counts."""
    repo = TradeRepository(session)
    counts = await repo.count_by_outcome()
    return {"counts": counts}
