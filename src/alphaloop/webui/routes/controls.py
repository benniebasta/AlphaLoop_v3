"""Durable controls and incident APIs."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from alphaloop.core.container import Container
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.risk.service import RiskService
from alphaloop.supervision.service import SupervisionService
from alphaloop.webui.deps import get_container

router = APIRouter(prefix="/api/controls", tags=["controls"])


class IncidentAckRequest(BaseModel):
    operator: str
    note: str = ""


class NoNewRiskClearRequest(BaseModel):
    operator: str
    note: str = ""


_NO_NEW_RISK_INCIDENT_REASON_MAP = {
    "bg_reconciliation_critical": "broker_db_split_brain",
    "bg_reconciliation_failure": "reconciler_failure",
    "order_recovery_block": "startup_order_recovery",
    "cross_instance_risk_block": "cross_instance_risk_unavailable",
}

_NO_NEW_RISK_REASON_POLICY = {
    "broker_db_split_brain": {
        "clear_prerequisite": (
            "recovery tombstones resolved and reconciler clean"
        ),
        "source_incidents": ["bg_reconciliation_critical"],
    },
    "reconciler_failure": {
        "clear_prerequisite": "reconciler clean state restored",
        "source_incidents": ["bg_reconciliation_failure"],
    },
    "startup_order_recovery": {
        "clear_prerequisite": "startup RECOVERY_PENDING orders resolved",
        "source_incidents": ["order_recovery_block"],
    },
    "cross_instance_risk_unavailable": {
        "clear_prerequisite": "shared-risk snapshot healthy and fresh",
        "source_incidents": ["cross_instance_risk_block"],
    },
    "operator_forced": {
        "clear_prerequisite": "authenticated operator acknowledgment with reason",
        "source_incidents": [],
    },
}


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


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for operator write actions when AUTH_TOKEN is set."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _build_risk_lock_state(service: SupervisionService) -> dict:
    """Derive operator-visible risk lock state from unresolved incidents."""
    incidents = await service.list_incidents(
        limit=200,
        include_acknowledged=True,
        include_resolved=False,
    )
    active_reasons: dict[str, list[int]] = {}
    incidents_by_reason: dict[str, list] = {}
    for incident in incidents:
        reason = _NO_NEW_RISK_INCIDENT_REASON_MAP.get(incident.incident_type)
        if reason is None:
            continue
        active_reasons.setdefault(reason, []).append(incident.id)
        incidents_by_reason.setdefault(reason, []).append(incident)

    reason_details = {}
    for reason, ids in sorted(active_reasons.items()):
        policy = _NO_NEW_RISK_REASON_POLICY.get(reason, {})
        source_incidents = incidents_by_reason.get(reason, [])
        clearable = bool(source_incidents) and all(
            incident.status == "ACKNOWLEDGED" for incident in source_incidents
        )
        reason_details[reason] = {
            "incident_ids": sorted(ids),
            "clearable": clearable,
            "clear_prerequisite": policy.get(
                "clear_prerequisite",
                "all active prerequisites resolved",
            ),
            "source_incidents": policy.get("source_incidents", []),
            "incident_statuses": {
                str(incident.id): incident.status for incident in source_incidents
            },
        }

    return {
        "no_new_risk_active": bool(active_reasons),
        "active_reasons": sorted(active_reasons),
        "reason_incident_ids": {
            reason: sorted(ids) for reason, ids in sorted(active_reasons.items())
        },
        "reason_details": reason_details,
        "compound_clearable": bool(active_reasons) and all(
            detail["clearable"] for detail in reason_details.values()
        ),
        "clear_rule": (
            "all active reasons must satisfy their prerequisites before no_new_risk can clear"
        ),
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
    http_request: Request,
    authorization: str = Header(default=""),
    container: Container = Depends(get_container),
) -> dict:
    _require_operator_auth(authorization)
    service = getattr(container, "supervision_service", None) or SupervisionService(container.db_session_factory)
    incident = await service.acknowledge_incident(
        incident_id,
        operator=request.operator,
        note=request.note,
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    try:
        async with container.db_session_factory() as session:
            session.add(OperatorAuditLog(
                operator=request.operator,
                action="incident_acknowledge",
                target=str(incident.id),
                old_value="OPEN",
                new_value="ACKNOWLEDGED",
                source_ip=http_request.client.host if http_request.client else "unknown",
            ))
            await session.commit()
    except Exception:
        # Do not hide the acknowledged incident state if audit logging degrades.
        pass

    return {"status": "ok", "incident": _serialize_incident(incident)}


@router.get("/portfolio")
async def get_controls_portfolio(
    container: Container = Depends(get_container),
) -> dict:
    service = getattr(container, "risk_service", None) or RiskService(container.db_session_factory)
    snapshot = await service.get_portfolio_snapshot()
    return snapshot.to_dict()


@router.get("/risk-state")
async def get_controls_risk_state(
    container: Container = Depends(get_container),
) -> dict:
    """Operator-facing view of active no-new-risk conditions."""
    supervision = getattr(container, "supervision_service", None) or SupervisionService(
        container.db_session_factory
    )
    return await _build_risk_lock_state(supervision)


@router.post("/no-new-risk/clear")
async def clear_no_new_risk(
    request: NoNewRiskClearRequest,
    http_request: Request,
    authorization: str = Header(default=""),
    container: Container = Depends(get_container),
) -> dict:
    """Resolve all active incident-backed no-new-risk reasons when clearable."""
    _require_operator_auth(authorization)
    supervision = getattr(container, "supervision_service", None) or SupervisionService(
        container.db_session_factory
    )
    risk_state = await _build_risk_lock_state(supervision)
    if not risk_state["no_new_risk_active"]:
        return {
            "status": "ok",
            "cleared": False,
            "message": "no_new_risk is not active",
            "risk_state": risk_state,
        }

    if not risk_state["compound_clearable"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "All active no_new_risk reasons must be acknowledged and clearable first",
                "risk_state": risk_state,
            },
        )

    incident_ids: list[int] = []
    for ids in risk_state["reason_incident_ids"].values():
        incident_ids.extend(ids)
    incident_ids = sorted(set(incident_ids))
    resolved = await supervision.resolve_incidents(
        incident_ids,
        operator=request.operator,
        note=request.note,
    )

    try:
        async with container.db_session_factory() as session:
            session.add(OperatorAuditLog(
                operator=request.operator,
                action="no_new_risk_clear",
                target=",".join(risk_state["active_reasons"]),
                old_value="ACTIVE",
                new_value="RESOLVED",
                source_ip=http_request.client.host if http_request.client else "unknown",
            ))
            await session.commit()
    except Exception:
        pass

    updated_state = await _build_risk_lock_state(supervision)
    return {
        "status": "ok",
        "cleared": True,
        "resolved_incident_ids": [incident.id for incident in resolved],
        "risk_state": updated_state,
    }
