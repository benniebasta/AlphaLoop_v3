"""GET /api/risk — Risk monitor status computed from trade history."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.trade import TradeLog
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("")
async def get_risk_status(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Compute risk metrics from trade history."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Daily trades
    daily_q = select(TradeLog).where(TradeLog.opened_at >= today_start)
    daily_trades = list((await session.execute(daily_q)).scalars())

    daily_pnl = sum(
        t.pnl_usd or 0 for t in daily_trades if t.outcome in ("WIN", "LOSS", "BE")
    )
    daily_wins = sum(1 for t in daily_trades if t.outcome == "WIN")
    daily_losses = sum(1 for t in daily_trades if t.outcome == "LOSS")
    open_count = sum(1 for t in daily_trades if t.outcome == "OPEN")

    # Consecutive losses (scan recent closed trades)
    recent_q = (
        select(TradeLog)
        .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
        .order_by(TradeLog.closed_at.desc())
        .limit(20)
    )
    recent = list((await session.execute(recent_q)).scalars())
    consec = 0
    for t in recent:
        if t.outcome == "LOSS":
            consec += 1
        else:
            break

    return {
        "daily_pnl": round(daily_pnl, 2),
        "daily_trades": len(daily_trades),
        "daily_wins": daily_wins,
        "daily_losses": daily_losses,
        "open_positions": open_count,
        "consecutive_losses": consec,
        "daily_win_rate": round(
            daily_wins / max(daily_wins + daily_losses, 1) * 100, 1
        ),
        "timestamp": now.isoformat(),
    }
