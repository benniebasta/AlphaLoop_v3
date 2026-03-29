"""Async repository for research reports and evolution events."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.research import (
    EvolutionEvent,
    ParameterSnapshot,
    ResearchReport,
)


class ResearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_report(self, **kwargs: Any) -> ResearchReport:
        report = ResearchReport(**kwargs)
        self._session.add(report)
        await self._session.flush()
        return report

    async def get_latest_reports(
        self,
        symbol: str | None = None,
        limit: int = 10,
    ) -> list[ResearchReport]:
        q = select(ResearchReport)
        if symbol:
            q = q.where(ResearchReport.symbol == symbol)
        q = q.order_by(ResearchReport.report_date.desc()).limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def create_snapshot(self, **kwargs: Any) -> ParameterSnapshot:
        snap = ParameterSnapshot(**kwargs)
        self._session.add(snap)
        await self._session.flush()
        return snap

    async def get_latest_snapshot(self) -> ParameterSnapshot | None:
        result = await self._session.execute(
            select(ParameterSnapshot)
            .order_by(ParameterSnapshot.snapped_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_evolution_event(self, **kwargs: Any) -> EvolutionEvent:
        evt = EvolutionEvent(**kwargs)
        self._session.add(evt)
        await self._session.flush()
        return evt

    async def get_evolution_events(
        self,
        symbol: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[EvolutionEvent]:
        q = select(EvolutionEvent)
        if symbol:
            q = q.where(EvolutionEvent.symbol == symbol)
        if event_type:
            q = q.where(EvolutionEvent.event_type == event_type)
        q = q.order_by(EvolutionEvent.occurred_at.desc()).limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())
