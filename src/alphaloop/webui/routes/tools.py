"""GET /api/tools — filter pipeline status, tool registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.pipeline import PipelineDecision, RejectionLog
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def pipeline_status(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Recent pipeline decisions and rejection summary."""
    # Recent decisions
    dec_q = (
        select(PipelineDecision)
        .order_by(PipelineDecision.occurred_at.desc())
        .limit(limit)
    )
    decisions = list((await session.execute(dec_q)).scalars())

    # Rejection counts by blocker
    rej_q = (
        select(RejectionLog.rejected_by, func.count())
        .group_by(RejectionLog.rejected_by)
    )
    rej_rows = (await session.execute(rej_q)).all()
    rejection_counts = {row[0] or "unknown": row[1] for row in rej_rows}

    return {
        "decisions": [
            {
                "id": d.id,
                "occurred_at": d.occurred_at.isoformat() if d.occurred_at else None,
                "symbol": d.symbol,
                "direction": d.direction,
                "allowed": d.allowed,
                "blocked_by": d.blocked_by,
                "block_reason": d.block_reason,
                "size_modifier": d.size_modifier,
            }
            for d in decisions
        ],
        "rejection_counts": rejection_counts,
    }


@router.get("/rejections")
async def recent_rejections(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Recent signal rejections."""
    q = (
        select(RejectionLog)
        .order_by(RejectionLog.occurred_at.desc())
        .limit(limit)
    )
    rejections = list((await session.execute(q)).scalars())
    return {
        "rejections": [
            {
                "id": r.id,
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "symbol": r.symbol,
                "direction": r.direction,
                "setup_type": r.setup_type,
                "rejected_by": r.rejected_by,
                "reason": r.reason,
            }
            for r in rejections
        ]
    }
