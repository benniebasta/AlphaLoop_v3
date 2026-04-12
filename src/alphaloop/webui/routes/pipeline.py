"""GET /api/pipeline — Gate-1 observability funnel + TradeDecision views.

Read-only endpoints built on top of the ``pipeline_stage_decisions`` ledger
(see ``db/models/pipeline.py::PipelineStageDecision``). No endpoint in this
file mutates state; they exist so the operator can see, in the UI, exactly
where trades are being blocked and why.

Endpoints:
    GET /api/pipeline/funnel
        Stage pass/reject counts, grouped by stage.  Filterable by symbol,
        source (``live`` | ``backtest_replay``), mode, and time window.

    GET /api/pipeline/decisions/latest
        Last N TradeDecision projections (rebuilt from the legacy
        ``pipeline_decisions.tool_results.trade_decision`` JSON).

    GET /api/pipeline/stages/heatmap
        Stage x symbol rejection-rate heatmap (last N cycles).

    GET /api/pipeline/modes/compare
        Per-mode funnel counts for a side-by-side comparison view.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.pipeline import PipelineDecision, PipelineStageDecision
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


# Canonical stage order for funnel rendering.  Any stage not in this list is
# appended after, so new stages surface without code changes.
_CANONICAL_STAGES = [
    "market_gate",
    "regime",
    "signal",
    "construction",
    "setup_policy",
    "invalidation",
    "quality",
    "conviction",
    "ai_validator",
    "risk_gate",
    "execution_guard",
    "freshness",
    "sizing",
    "shadow_mode",
    "pipeline",
]


def _parse_window(since: str | None, hours: int) -> datetime:
    if since:
        try:
            return datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc) - timedelta(hours=max(1, hours))


def _stage_sort_key(stage: str) -> tuple[int, str]:
    try:
        return (_CANONICAL_STAGES.index(stage), stage)
    except ValueError:
        return (len(_CANONICAL_STAGES), stage)


@router.get("/funnel")
async def get_pipeline_funnel(
    session: AsyncSession = Depends(get_db_session),
    symbol: str | None = Query(default=None),
    source: str = Query(default="live"),
    mode: str | None = Query(default=None),
    hours: int = Query(default=24, ge=1, le=24 * 30),
    since: str | None = Query(default=None),
) -> dict:
    """Stage pass/reject counts for the requested window.

    Returns one entry per stage with ``passed``, ``blocked``, ``held`` and
    ``other`` counts plus the top rejection reason codes observed.
    """
    window_start = _parse_window(since, hours)

    filters = [PipelineStageDecision.occurred_at >= window_start]
    if source:
        filters.append(PipelineStageDecision.source == source)
    if symbol:
        filters.append(PipelineStageDecision.symbol == symbol)
    if mode:
        filters.append(PipelineStageDecision.mode == mode)

    rows_q = select(
        PipelineStageDecision.stage,
        PipelineStageDecision.status,
        PipelineStageDecision.blocked_by,
        func.count().label("n"),
    ).where(and_(*filters)).group_by(
        PipelineStageDecision.stage,
        PipelineStageDecision.status,
        PipelineStageDecision.blocked_by,
    )
    rows = (await session.execute(rows_q)).all()

    stage_agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "stage": "",
            "total": 0,
            "passed": 0,
            "blocked": 0,
            "held": 0,
            "other": 0,
            "reasons": defaultdict(int),
        }
    )
    for stage, status, blocked_by, n in rows:
        bucket = stage_agg[stage]
        bucket["stage"] = stage
        bucket["total"] += n
        status_norm = (status or "").lower()
        if status_norm in ("passed", "classified", "scored", "hypothesis_generated",
                           "signal_generated", "constructed", "trade", "approved",
                           "pass", "execute", "computed"):
            bucket["passed"] += n
        elif status_norm in ("blocked", "rejected", "hard_invalidated", "block"):
            bucket["blocked"] += n
            if blocked_by:
                bucket["reasons"][blocked_by] += n
        elif status_norm in ("held", "hold", "no_signal", "no_construction",
                             "soft_invalidated"):
            bucket["held"] += n
            if blocked_by:
                bucket["reasons"][blocked_by] += n
        elif status_norm == "delay":
            bucket["other"] += n
        else:
            bucket["other"] += n

    # Normalise + sort.
    stages_out: list[dict[str, Any]] = []
    for stage, bucket in stage_agg.items():
        top_reasons = sorted(
            bucket["reasons"].items(), key=lambda kv: kv[1], reverse=True
        )[:5]
        stages_out.append(
            {
                "stage": stage,
                "total": bucket["total"],
                "passed": bucket["passed"],
                "blocked": bucket["blocked"],
                "held": bucket["held"],
                "other": bucket["other"],
                "top_reasons": [
                    {"reason": r, "count": c} for r, c in top_reasons
                ],
            }
        )
    stages_out.sort(key=lambda s: _stage_sort_key(s["stage"]))

    # Cycle-level totals for the funnel header.
    total_cycles_q = (
        select(func.count(func.distinct(PipelineStageDecision.cycle_id)))
        .where(and_(*filters))
    )
    total_cycles = int((await session.execute(total_cycles_q)).scalar() or 0)

    executed_q = (
        select(func.count(func.distinct(PipelineStageDecision.cycle_id)))
        .where(and_(*filters, PipelineStageDecision.outcome == "trade_opened"))
    )
    executed = int((await session.execute(executed_q)).scalar() or 0)

    return {
        "window_start": window_start.isoformat(),
        "window_end": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "symbol": symbol,
            "source": source,
            "mode": mode,
            "hours": hours,
        },
        "total_cycles": total_cycles,
        "executed_cycles": executed,
        "stages": stages_out,
    }


@router.get("/decisions/latest")
async def get_latest_decisions(
    session: AsyncSession = Depends(get_db_session),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=500),
) -> dict:
    """Return the most recent TradeDecision projections for the decision panel."""
    q = select(PipelineDecision).order_by(PipelineDecision.occurred_at.desc()).limit(limit)
    if symbol:
        q = (
            select(PipelineDecision)
            .where(PipelineDecision.symbol == symbol)
            .order_by(PipelineDecision.occurred_at.desc())
            .limit(limit)
        )
    rows = list((await session.execute(q)).scalars())

    decisions: list[dict[str, Any]] = []
    for row in rows:
        tool_results = row.tool_results or {}
        td = tool_results.get("trade_decision") if isinstance(tool_results, dict) else None
        if td is None:
            # Legacy row (pre-Gate-1) — build a minimal shape from what we have.
            td = {
                "symbol": row.symbol,
                "mode": None,
                "direction": row.direction,
                "setup_type": None,
                "outcome": "trade_opened" if row.allowed else "rejected",
                "reject_stage": row.blocked_by,
                "reject_reason": row.block_reason,
                "confidence_raw": None,
                "confidence_adjusted": None,
                "conviction_score": None,
                "conviction_decision": None,
                "penalties": [],
                "size_multiplier": row.size_modifier,
                "hard_block": not row.allowed,
                "ai_verdict": "skipped",
                "execution_status": "executed" if row.allowed else "blocked",
                "latency_ms": 0.0,
                "journey": (tool_results.get("journey") if isinstance(tool_results, dict) else None),
                "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            }
        decisions.append(
            {
                "id": row.id,
                "decision": td,
            }
        )
    return {"count": len(decisions), "decisions": decisions}


@router.get("/stages/heatmap")
async def get_stage_heatmap(
    session: AsyncSession = Depends(get_db_session),
    source: str = Query(default="live"),
    hours: int = Query(default=24, ge=1, le=24 * 30),
) -> dict:
    """Rejection-rate heatmap (stage x symbol) for the last N hours."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=hours)

    q = select(
        PipelineStageDecision.stage,
        PipelineStageDecision.symbol,
        PipelineStageDecision.status,
        func.count().label("n"),
    ).where(
        and_(
            PipelineStageDecision.occurred_at >= window_start,
            PipelineStageDecision.source == source,
        )
    ).group_by(
        PipelineStageDecision.stage,
        PipelineStageDecision.symbol,
        PipelineStageDecision.status,
    )
    rows = (await session.execute(q)).all()

    cell: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "blocked": 0, "held": 0}
    )
    symbols_set: set[str] = set()
    stages_set: set[str] = set()
    for stage, sym, status, n in rows:
        sym = sym or "?"
        symbols_set.add(sym)
        stages_set.add(stage)
        key = (stage, sym)
        cell[key]["total"] += n
        status_norm = (status or "").lower()
        if status_norm in ("blocked", "rejected", "hard_invalidated"):
            cell[key]["blocked"] += n
        elif status_norm in ("held", "hold", "no_signal", "no_construction", "soft_invalidated"):
            cell[key]["held"] += n

    stages_sorted = sorted(stages_set, key=_stage_sort_key)
    symbols_sorted = sorted(symbols_set)

    cells_out: list[dict[str, Any]] = []
    for stage in stages_sorted:
        for sym in symbols_sorted:
            b = cell.get((stage, sym))
            if not b:
                continue
            rej_rate = (b["blocked"] + b["held"]) / b["total"] if b["total"] else 0.0
            cells_out.append(
                {
                    "stage": stage,
                    "symbol": sym,
                    "total": b["total"],
                    "blocked": b["blocked"],
                    "held": b["held"],
                    "rejection_rate": round(rej_rate, 4),
                }
            )

    return {
        "window_start": window_start.isoformat(),
        "stages": stages_sorted,
        "symbols": symbols_sorted,
        "cells": cells_out,
    }


@router.get("/modes/compare")
async def compare_modes(
    session: AsyncSession = Depends(get_db_session),
    symbol: str | None = Query(default=None),
    source: str = Query(default="live"),
    hours: int = Query(default=24, ge=1, le=24 * 30),
) -> dict:
    """Per-mode funnel counts for algo_only / algo_ai / ai_signal comparison."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=hours)
    filters = [
        PipelineStageDecision.occurred_at >= window_start,
        PipelineStageDecision.source == source,
    ]
    if symbol:
        filters.append(PipelineStageDecision.symbol == symbol)

    q = select(
        PipelineStageDecision.mode,
        PipelineStageDecision.outcome,
        func.count(func.distinct(PipelineStageDecision.cycle_id)).label("n"),
    ).where(and_(*filters)).group_by(
        PipelineStageDecision.mode,
        PipelineStageDecision.outcome,
    )
    rows = (await session.execute(q)).all()

    by_mode: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for mode, outcome, n in rows:
        by_mode[mode or "unknown"][outcome or "unknown"] = int(n)

    return {
        "window_start": window_start.isoformat(),
        "filters": {"symbol": symbol, "source": source, "hours": hours},
        "modes": [
            {
                "mode": mode,
                "outcomes": dict(outcomes),
                "total": sum(outcomes.values()),
                "executed": outcomes.get("trade_opened", 0),
            }
            for mode, outcomes in by_mode.items()
        ],
    }
