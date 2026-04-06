"""GET /api/alerts — view and manage alert engine alerts."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for alert write actions when AUTH_TOKEN is configured."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


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
async def acknowledge_alert(
    request: Request,
    index: int,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Acknowledge an alert by index."""
    _require_operator_auth(authorization)
    engine = getattr(request.app.state.container, "alert_engine", None)
    if engine is None:
        return {"status": "error", "message": "AlertEngine not available"}
    ok = engine.acknowledge(index)
    if ok:
        session.add(OperatorAuditLog(
            operator="webui",
            action="alert_acknowledge",
            target=str(index),
            old_value="active",
            new_value="acknowledged",
            source_ip=request.client.host if request.client else "unknown",
        ))
        await session.commit()
    return {"status": "ok" if ok else "not_found"}
