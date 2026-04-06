"""Retention service for durable pipeline decision journeys."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, select

from alphaloop.db.models.pipeline import PipelineDecision, PipelineDecisionArchive


@dataclass
class PipelineRetentionReport:
    """Summary of one archive/purge run."""

    cutoff: datetime
    archived_count: int = 0
    purged_count: int = 0
    skipped: bool = False
    skip_reason: str | None = None


class PipelineJourneyRetentionService:
    """Moves expired pipeline decisions to cold storage and purges hot rows."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def archive_expired_decisions(
        self,
        *,
        retention_days: int = 30,
        batch_size: int = 500,
        now: datetime | None = None,
    ) -> PipelineRetentionReport:
        if retention_days < 1:
            raise ValueError("retention_days must be >= 1")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        now_utc = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(days=retention_days)
        report = PipelineRetentionReport(cutoff=cutoff)

        async with self._session_factory() as session:
            if not await self._required_tables_available(session):
                report.skipped = True
                report.skip_reason = "required pipeline retention tables missing"
                return report

            result = await session.execute(
                select(PipelineDecision)
                .where(PipelineDecision.occurred_at < cutoff)
                .order_by(PipelineDecision.occurred_at.asc(), PipelineDecision.id.asc())
                .limit(batch_size)
            )
            expired = list(result.scalars())

            for decision in expired:
                session.add(
                    PipelineDecisionArchive(
                        original_decision_id=decision.id,
                        occurred_at=decision.occurred_at,
                        symbol=decision.symbol,
                        direction=decision.direction,
                        allowed=decision.allowed,
                        blocked_by=decision.blocked_by,
                        block_reason=decision.block_reason,
                        size_modifier=decision.size_modifier,
                        bias=decision.bias,
                        tool_results=decision.tool_results,
                        instance_id=decision.instance_id,
                    )
                )
                await session.delete(decision)

            await session.commit()
            report.archived_count = len(expired)
            report.purged_count = len(expired)
            return report

    @staticmethod
    async def _required_tables_available(session) -> bool:
        conn = await session.connection()

        def _check_tables(sync_conn) -> bool:
            inspector = inspect(sync_conn)
            return (
                inspector.has_table("pipeline_decisions")
                and inspector.has_table("pipeline_decision_archive")
            )

        return await conn.run_sync(_check_tables)
