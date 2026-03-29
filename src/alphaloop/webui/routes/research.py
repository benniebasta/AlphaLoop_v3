"""GET /api/research — reports, evolution events."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.repositories.research_repo import ResearchRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("")
async def get_reports(
    symbol: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return recent research reports."""
    repo = ResearchRepository(session)
    reports = await repo.get_latest_reports(symbol=symbol, limit=limit)
    return {
        "reports": [
            {
                "id": r.id,
                "symbol": r.symbol,
                "strategy_version": r.strategy_version,
                "report_date": r.report_date.isoformat() if r.report_date else None,
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "avg_rr": r.avg_rr,
                "total_pnl_usd": r.total_pnl_usd,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown_pct": r.max_drawdown_pct,
                "analysis_summary": r.analysis_summary,
            }
            for r in reports
        ]
    }


@router.get("/evolution")
async def get_evolution_events(
    symbol: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return evolution events (parameter tuning, rollbacks, etc.)."""
    repo = ResearchRepository(session)
    events = await repo.get_evolution_events(
        symbol=symbol, event_type=event_type, limit=limit
    )
    return {
        "events": [
            {
                "id": e.id,
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                "symbol": e.symbol,
                "strategy_version": e.strategy_version,
                "event_type": e.event_type,
                "details": e.details,
                "params_before": e.params_before,
                "params_after": e.params_after,
            }
            for e in events
        ]
    }
