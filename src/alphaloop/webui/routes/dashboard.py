"""GET /api/dashboard — overview stats."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.trade import TradeLog
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard_stats(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return overview stats: open trades, daily PnL, win rate, balance."""
    # Open trades count
    open_q = select(func.count()).where(TradeLog.outcome == "OPEN")
    open_count = (await session.execute(open_q)).scalar() or 0

    # Today's closed trades
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    daily_q = (
        select(TradeLog)
        .where(TradeLog.closed_at >= today_start)
        .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
    )
    daily_trades = list((await session.execute(daily_q)).scalars())

    daily_pnl = sum(t.pnl_usd or 0.0 for t in daily_trades)
    daily_wins = sum(1 for t in daily_trades if t.outcome == "WIN")
    daily_total = len(daily_trades)
    daily_win_rate = (daily_wins / daily_total * 100) if daily_total else 0.0

    # All-time stats
    all_q = select(TradeLog).where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
    all_trades = list((await session.execute(all_q)).scalars())
    total_pnl = sum(t.pnl_usd or 0.0 for t in all_trades)
    total_wins = sum(1 for t in all_trades if t.outcome == "WIN")
    total_count = len(all_trades)
    overall_win_rate = (total_wins / total_count * 100) if total_count else 0.0

    # Recent 7-day PnL
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    week_q = (
        select(func.coalesce(func.sum(TradeLog.pnl_usd), 0.0))
        .where(TradeLog.closed_at >= week_ago)
        .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
    )
    weekly_pnl = (await session.execute(week_q)).scalar() or 0.0

    return {
        "open_trades": open_count,
        "daily_pnl": round(daily_pnl, 2),
        "daily_trades": daily_total,
        "daily_win_rate": round(daily_win_rate, 1),
        "weekly_pnl": round(float(weekly_pnl), 2),
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_count,
        "overall_win_rate": round(overall_win_rate, 1),
    }
