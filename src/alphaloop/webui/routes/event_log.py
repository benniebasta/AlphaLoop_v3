"""GET /api/events — Recent event bus activity."""

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/events", tags=["events"])
logger = logging.getLogger(__name__)

_event_log: deque[dict] = deque(maxlen=200)
_waterfall_log: deque[dict] = deque(maxlen=100)


def record_event(event) -> None:
    """Called by event bus handler to log events into the ring buffer."""
    _event_log.appendleft(
        {
            "type": type(event).__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                k: v for k, v in vars(event).items() if not k.startswith("_")
            },
        }
    )


def record_waterfall(pipeline_result) -> None:
    """
    Record a v4 pipeline result as a waterfall entry.

    Called from the trading loop after each v4 cycle completes.
    Provides the full score breakdown for debugging.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": pipeline_result.outcome.value if hasattr(pipeline_result.outcome, "value") else str(pipeline_result.outcome),
        "elapsed_ms": pipeline_result.elapsed_ms,
        "rejection_reason": pipeline_result.rejection_reason,
        "journey": pipeline_result.journey.to_dict() if getattr(pipeline_result, "journey", None) else None,
    }

    # Regime
    if pipeline_result.regime:
        r = pipeline_result.regime
        entry["regime"] = {
            "regime": r.regime,
            "macro": r.macro_regime,
            "volatility": r.volatility_band,
            "session_quality": r.session_quality,
            "size_multiplier": r.size_multiplier,
            "allowed_setups": r.allowed_setups,
        }

    # Signal
    if pipeline_result.signal:
        s = pipeline_result.signal
        entry["signal"] = {
            "direction": s.direction,
            "setup_type": s.setup_type,
            "entry_zone": list(s.entry_zone),
            "stop_loss": s.stop_loss,
            "take_profit": s.take_profit,
            "raw_confidence": s.raw_confidence,
            "rr_ratio": s.rr_ratio,
        }

    # Invalidation
    if pipeline_result.invalidation:
        inv = pipeline_result.invalidation
        entry["invalidation"] = {
            "severity": inv.severity,
            "failures": [
                {"check": f.check_name, "severity": f.severity, "reason": f.reason}
                for f in inv.failures
            ],
            "conviction_penalty": inv.conviction_penalty,
        }

    # Quality
    if pipeline_result.quality:
        q = pipeline_result.quality
        entry["quality"] = {
            "overall_score": q.overall_score,
            "group_scores": q.group_scores,
            "tool_scores": q.tool_scores,
            "contradictions": q.contradictions,
            "max_score": q.max_score,
        }

    # Conviction (the core waterfall)
    if pipeline_result.conviction:
        c = pipeline_result.conviction
        entry["conviction"] = {
            "score": c.score,
            "decision": c.decision,
            "size_scalar": c.size_scalar,
            "hold_reason": c.hold_reason,
            "group_contributions": c.group_contributions,
            "conflict_penalty": c.conflict_penalty,
            "invalidation_penalty": c.invalidation_penalty,
            "portfolio_penalty": c.portfolio_penalty,
            "total_penalty": c.total_penalty,
            "penalty_budget_used": c.penalty_budget_used,
            "penalty_budget_cap": c.penalty_budget_cap,
            "penalties_prorated": c.penalties_prorated,
            "quality_floor_triggered": c.quality_floor_triggered,
            "regime_min_entry": c.regime_min_entry,
            "regime_ceiling": c.regime_ceiling,
        }

    # Sizing
    if pipeline_result.sizing:
        sz = pipeline_result.sizing
        entry["sizing"] = {
            "conviction_scalar": sz.conviction_scalar,
            "regime_scalar": sz.regime_scalar,
            "freshness_scalar": sz.freshness_scalar,
            "risk_gate_scalar": sz.risk_gate_scalar,
            "equity_curve_scalar": sz.equity_curve_scalar,
        }

    _waterfall_log.appendleft(entry)


@router.get("")
async def get_events(
    request: Request,
    limit: int = Query(default=50, le=200),
    event_type: str | None = Query(default=None, alias="type"),
    symbol: str | None = Query(default=None),
    instance_id: str | None = Query(default=None),
    from_db: bool = Query(default=False, description="Read from persistent DB instead of in-memory ring buffer"),
) -> dict:
    """Return recent events, optionally filtered by type, symbol, or instance.

    By default reads the in-memory ring buffer (fast, resets on restart).
    Pass ?from_db=true to read from the durable operational_events table (survives restarts).
    Note: cycle events (CycleStarted, PipelineStep, etc.) are in-memory only — they
    are never persisted to the DB, so from_db=true will not return them.
    """
    use_db = from_db

    if use_db:
        try:
            supervision = getattr(
                getattr(request.app.state, "container", None),
                "supervision_service",
                None,
            )
            if supervision is not None:
                category_filter = None
                if event_type:
                    category_filter = None  # event_type maps to event_type col, not category
                db_events_raw = await supervision.list_events(limit=min(limit, 200))
                db_events = []
                for e in db_events_raw:
                    entry = {
                        "type": e.event_type,
                        "timestamp": e.created_at.isoformat() if e.created_at else "",
                        "data": e.payload or {},
                        "source": "db",
                    }
                    if event_type and entry["type"] != event_type:
                        continue
                    if instance_id and entry["data"].get("instance_id") != instance_id:
                        continue
                    if symbol and entry["data"].get("symbol") != symbol:
                        continue
                    db_events.append(entry)
                return {"events": db_events[:limit], "total": len(db_events), "source": "db"}
        except Exception as exc:
            logger.warning("[events] DB fallback failed, using in-memory: %s", exc)

    events = list(_event_log)
    if event_type:
        events = [e for e in events if e["type"] == event_type]
    if instance_id:
        events = [e for e in events if e.get("data", {}).get("instance_id") == instance_id]
    elif symbol:
        events = [e for e in events if e.get("data", {}).get("symbol") == symbol]
    return {"events": events[:limit], "total": len(_event_log), "source": "memory"}


@router.get("/waterfall")
async def get_waterfall(
    limit: int = Query(default=20, le=100),
    outcome: str | None = Query(default=None),
) -> dict:
    """
    Return recent v4 pipeline waterfall entries.

    Each entry contains the full score breakdown: regime, signal,
    invalidation, quality scores, conviction penalties, and sizing scalars.
    """
    entries = list(_waterfall_log)
    if outcome:
        entries = [e for e in entries if e.get("outcome") == outcome]
    return {"waterfalls": entries[:limit], "total": len(_waterfall_log)}


@router.delete("")
async def clear_events(
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Clear all events and waterfall entries from the ring buffers."""
    _check_ingest_token(authorization)
    cleared_events = len(_event_log)
    cleared_waterfalls = len(_waterfall_log)
    _event_log.clear()
    _waterfall_log.clear()
    session.add(OperatorAuditLog(
        operator="webui",
        action="event_log_clear",
        target="event_buffers",
        old_value=f"events={cleared_events},waterfalls={cleared_waterfalls}",
        new_value="cleared",
        source_ip=request.client.host if request.client else "unknown",
    ))
    await session.commit()
    return {"ok": True}


def _check_ingest_token(authorization: str) -> None:
    """H-08: Verify Bearer token on ingest endpoint.

    If AUTH_TOKEN is not set (dev mode), all requests are allowed through.
    """
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return  # Dev mode — no auth required
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/ingest")
async def ingest_event(
    body: dict,
    authorization: str = Header(default=""),
) -> dict:
    """Accept events POSTed from subprocess trading agents.

    Requires ``Authorization: Bearer <AUTH_TOKEN>`` when AUTH_TOKEN is set.
    In dev mode (AUTH_TOKEN unset), the header is optional.
    """
    _check_ingest_token(authorization)
    entry = {
        "type": body.get("type", "Unknown"),
        "timestamp": body.get("timestamp", datetime.now(timezone.utc).isoformat()),
        "data": body.get("data", {}),
    }
    _event_log.appendleft(entry)

    # Broadcast to WebSocket clients for real-time updates
    from alphaloop.webui.routes.websocket import broadcast
    await broadcast(entry)

    return {"status": "ok"}
