"""GET /api/alerts — view and manage alert engine alerts."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def get_alerts(request: Request, limit: int = 50) -> dict:
    """Return all alerts and rules summary."""
    engine = getattr(request.app.state.container, "alert_engine", None)
    if engine is None:
        return {"alerts": [], "rules": [], "message": "AlertEngine not available (web-only mode)"}
    return {
        "alerts": engine.get_all_alerts(limit=limit),
        "active": engine.get_active_alerts(),
        "rules": engine.rules_summary,
    }


@router.post("/acknowledge/{index}")
async def acknowledge_alert(request: Request, index: int) -> dict:
    """Acknowledge an alert by index."""
    engine = getattr(request.app.state.container, "alert_engine", None)
    if engine is None:
        return {"status": "error", "message": "AlertEngine not available"}
    ok = engine.acknowledge(index)
    return {"status": "ok" if ok else "not_found"}
