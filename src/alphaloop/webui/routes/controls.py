"""Durable controls and incident APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from alphaloop.core.container import Container
from alphaloop.risk.service import RiskService
from alphaloop.supervision.service import SupervisionService
from alphaloop.webui.deps import get_container

router = APIRouter(prefix="/api/controls", tags=["controls"])


class IncidentAckRequest(BaseModel):
    operator: str
    note: str = ""


def _serialize_incident(incident) -> dict:
    return {
        "id": incident.id,
        "incident_type": incident.incident_type,
        "status": incident.status,
        "severity": incident.severity,
        "title": incident.title,
        "details": incident.details,
        "symbol": incident.symbol,
        "instance_id": incident.instance_id,
        "source": incident.source,
        "payload": incident.payload or {},
        "acknowledged_by": incident.acknowledged_by,
        "acknowledged_note": incident.acknowledged_note,
        "acknowledged_at": incident.acknowledged_at.isoformat() if incident.acknowledged_at else None,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
    }


@router.get("/incidents")
async def list_incidents(
    limit: int = 100,
    include_acknowledged: bool = True,
    container: Container = Depends(get_container),
) -> dict:
    service = getattr(container, "supervision_service", None) or SupervisionService(container.db_session_factory)
    incidents = await service.list_incidents(
        limit=limit,
        include_acknowledged=include_acknowledged,
    )
    return {
        "incidents": [_serialize_incident(incident) for incident in incidents],
        "count": len(incidents),
    }


@router.post("/incidents/{incident_id}/ack")
async def acknowledge_incident(
    incident_id: int,
    request: IncidentAckRequest,
    container: Container = Depends(get_container),
) -> dict:
    service = getattr(container, "supervision_service", None) or SupervisionService(container.db_session_factory)
    incident = await service.acknowledge_incident(
        incident_id,
        operator=request.operator,
        note=request.note,
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {"status": "ok", "incident": _serialize_incident(incident)}


@router.get("/portfolio")
async def get_controls_portfolio(
    container: Container = Depends(get_container),
) -> dict:
    service = getattr(container, "risk_service", None) or RiskService(container.db_session_factory)
    snapshot = await service.get_portfolio_snapshot()
    return snapshot.to_dict()
