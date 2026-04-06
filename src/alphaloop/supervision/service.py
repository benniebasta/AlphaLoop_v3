"""Persisted supervision services for incidents and event outbox."""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from alphaloop.db.models.incident import IncidentRecord
from alphaloop.db.models.operational_event import OperationalEvent

logger = logging.getLogger(__name__)


class SupervisionService:
    """Stores durable incidents and operational events."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def record_event(
        self,
        *,
        category: str,
        event_type: str,
        severity: str = "info",
        symbol: str | None = None,
        instance_id: str | None = None,
        entity_id: str | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int | None:
        try:
            async with self._session_factory() as session:
                event = OperationalEvent(
                    category=category,
                    event_type=event_type,
                    severity=severity,
                    symbol=symbol,
                    instance_id=instance_id,
                    entity_id=entity_id,
                    message=message,
                    payload=payload,
                )
                session.add(event)
                await session.commit()
                return event.id
        except Exception as exc:
            logger.warning("[supervision] Failed to record event %s: %s", event_type, exc)
            return None

    async def record_bus_event(self, event) -> None:
        payload = self._serialize_payload(event)
        await self.record_event(
            category="event_bus",
            event_type=type(event).__name__,
            severity="info",
            symbol=payload.get("symbol"),
            instance_id=payload.get("instance_id"),
            message=payload.get("detail") or payload.get("reason") or payload.get("details"),
            payload=payload,
        )

    async def record_incident(
        self,
        *,
        incident_type: str,
        details: str,
        severity: str = "critical",
        title: str | None = None,
        symbol: str | None = None,
        instance_id: str | None = None,
        source: str = "system",
        payload: dict[str, Any] | None = None,
        status: str = "OPEN",
    ) -> int | None:
        try:
            async with self._session_factory() as session:
                incident = IncidentRecord(
                    incident_type=incident_type,
                    status=status,
                    severity=severity,
                    title=title or incident_type.replace("_", " ").title(),
                    details=details,
                    symbol=symbol,
                    instance_id=instance_id,
                    source=source,
                    payload=payload,
                )
                session.add(incident)
                await session.commit()
                incident_id = incident.id
        except Exception as exc:
            logger.warning("[supervision] Failed to record incident %s: %s", incident_type, exc)
            return None

        await self.record_event(
            category="incident",
            event_type=incident_type,
            severity=severity,
            symbol=symbol,
            instance_id=instance_id,
            entity_id=str(incident_id),
            message=details,
            payload=payload or {},
        )
        return incident_id

    async def acknowledge_incident(
        self,
        incident_id: int,
        *,
        operator: str,
        note: str = "",
    ) -> IncidentRecord | None:
        async with self._session_factory() as session:
            incident = await session.get(IncidentRecord, incident_id)
            if incident is None:
                return None
            incident.status = "ACKNOWLEDGED"
            incident.acknowledged_by = operator
            incident.acknowledged_note = note
            incident.acknowledged_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(incident)
            payload = {
                "incident_id": incident.id,
                "incident_type": incident.incident_type,
                "operator": operator,
                "note": note,
            }

        await self.record_event(
            category="operator_action",
            event_type="incident_acknowledged",
            severity="info",
            symbol=incident.symbol,
            instance_id=incident.instance_id,
            entity_id=str(incident.id),
            message=f"Incident {incident.id} acknowledged by {operator}",
            payload=payload,
        )
        return incident

    async def list_incidents(
        self,
        *,
        limit: int = 100,
        include_acknowledged: bool = True,
        include_resolved: bool = False,
    ) -> list[IncidentRecord]:
        async with self._session_factory() as session:
            query = select(IncidentRecord).order_by(IncidentRecord.created_at.desc())
            if not include_resolved:
                query = query.where(IncidentRecord.status != "RESOLVED")
            if not include_acknowledged:
                query = query.where(IncidentRecord.status != "ACKNOWLEDGED")
            query = query.limit(limit)
            result = await session.execute(query)
            return list(result.scalars())

    async def resolve_incidents(
        self,
        incident_ids: list[int],
        *,
        operator: str,
        note: str = "",
    ) -> list[IncidentRecord]:
        if not incident_ids:
            return []

        async with self._session_factory() as session:
            query = select(IncidentRecord).where(IncidentRecord.id.in_(incident_ids))
            result = await session.execute(query)
            incidents = list(result.scalars())
            for incident in incidents:
                incident.status = "RESOLVED"
                incident.acknowledged_by = operator
                incident.acknowledged_note = note
                incident.acknowledged_at = datetime.now(timezone.utc)
            await session.commit()
            for incident in incidents:
                await session.refresh(incident)

        for incident in incidents:
            await self.record_event(
                category="operator_action",
                event_type="incident_resolved",
                severity="info",
                symbol=incident.symbol,
                instance_id=incident.instance_id,
                entity_id=str(incident.id),
                message=f"Incident {incident.id} resolved by {operator}",
                payload={
                    "incident_id": incident.id,
                    "incident_type": incident.incident_type,
                    "operator": operator,
                    "note": note,
                },
            )
        return incidents

    async def list_events(
        self,
        *,
        category: str | None = None,
        limit: int = 100,
    ) -> list[OperationalEvent]:
        async with self._session_factory() as session:
            query = select(OperationalEvent).order_by(OperationalEvent.created_at.desc())
            if category:
                query = query.where(OperationalEvent.category == category)
            query = query.limit(limit)
            result = await session.execute(query)
            return list(result.scalars())

    @staticmethod
    def _serialize_payload(event) -> dict[str, Any]:
        try:
            raw = dataclasses.asdict(event)
        except TypeError:
            raw = {
                k: v for k, v in vars(event).items()
                if not k.startswith("_")
            }
        payload: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    # naive datetime — assume UTC rather than local time
                    from datetime import timezone as _tz
                    value = value.replace(tzinfo=_tz.utc)
                payload[key] = value.astimezone(timezone.utc).isoformat()
            elif isinstance(value, (str, int, float, bool)) or value is None:
                payload[key] = value
            else:
                payload[key] = str(value)
        return payload
