"""
Data retention and archival service.

Retention policy:
  pipeline_decisions     → archive to pipeline_decision_archive (90 days),
                           then delete from source after archival
  pipeline_stage_decisions → delete after 90 days (no archive table)
  signal_log             → delete after 90 days
  operational_events     → delete after 30 days
  trade_logs             → retain indefinitely (2yr+ historical value)
  trade_audit_log        → retain indefinitely
  operator_audit_log     → retain indefinitely (compliance trail)

Usage (called from app lifespan, weekly):

    from alphaloop.db.archival import run_archival_cycle
    await run_archival_cycle(session_factory)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retention windows
# ---------------------------------------------------------------------------

_PIPELINE_DECISIONS_DAYS = 90
_PIPELINE_STAGE_DECISIONS_DAYS = 90
_SIGNAL_LOG_DAYS = 90
_OPERATIONAL_EVENTS_DAYS = 30


# ---------------------------------------------------------------------------
# Individual archival / purge tasks
# ---------------------------------------------------------------------------

async def _archive_pipeline_decisions(
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    """Move pipeline_decisions older than cutoff to pipeline_decision_archive."""
    from alphaloop.db.models.pipeline import PipelineDecision, PipelineDecisionArchive

    rows = (await session.execute(
        select(PipelineDecision).where(PipelineDecision.occurred_at < cutoff)
    )).scalars().all()

    if not rows:
        return 0

    # Insert into archive (skip duplicates based on original_decision_id)
    existing_ids: set[int] = set(
        (await session.execute(
            select(PipelineDecisionArchive.original_decision_id)
            .where(PipelineDecisionArchive.original_decision_id.in_([r.id for r in rows]))
        )).scalars().all()
    )

    to_archive = [r for r in rows if r.id not in existing_ids]
    if to_archive:
        await session.execute(
            insert(PipelineDecisionArchive),
            [
                {
                    "original_decision_id": r.id,
                    "occurred_at": r.occurred_at,
                    "archived_at": datetime.now(timezone.utc),
                    "symbol": r.symbol,
                    "direction": r.direction,
                    "allowed": r.allowed,
                    "blocked_by": r.blocked_by,
                    "block_reason": r.block_reason,
                    "size_modifier": r.size_modifier,
                    "bias": r.bias,
                    "tool_results": r.tool_results,
                    "instance_id": r.instance_id,
                }
                for r in to_archive
            ],
        )

    # Delete from source
    ids_to_delete = [r.id for r in rows]
    await session.execute(
        delete(PipelineDecision).where(PipelineDecision.id.in_(ids_to_delete))
    )

    return len(rows)


async def _purge_pipeline_stage_decisions(
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    """Delete pipeline_stage_decisions older than cutoff."""
    from alphaloop.db.models.pipeline import PipelineStageDecision

    result = await session.execute(
        delete(PipelineStageDecision).where(PipelineStageDecision.occurred_at < cutoff)
    )
    return result.rowcount or 0


async def _purge_signal_log(
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    """Delete signal_log rows older than cutoff."""
    from alphaloop.db.models.signal_log import SignalLog

    result = await session.execute(
        delete(SignalLog).where(SignalLog.created_at < cutoff)
    )
    return result.rowcount or 0


async def _purge_operational_events(
    session: AsyncSession,
    cutoff: datetime,
) -> int:
    """Delete operational_events older than cutoff."""
    from alphaloop.db.models.operational_event import OperationalEvent

    result = await session.execute(
        delete(OperationalEvent).where(OperationalEvent.created_at < cutoff)
    )
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_archival_cycle(session_factory) -> dict[str, int]:
    """
    Execute one full archival/purge cycle against all retention-managed tables.

    Returns a summary dict with row counts processed per table.
    Commits atomically — a failure in one step does not affect others.
    """
    now = datetime.now(timezone.utc)

    cutoffs = {
        "pipeline_decisions": now - timedelta(days=_PIPELINE_DECISIONS_DAYS),
        "pipeline_stage_decisions": now - timedelta(days=_PIPELINE_STAGE_DECISIONS_DAYS),
        "signal_log": now - timedelta(days=_SIGNAL_LOG_DAYS),
        "operational_events": now - timedelta(days=_OPERATIONAL_EVENTS_DAYS),
    }

    summary: dict[str, int] = {}

    tasks = [
        ("pipeline_decisions", _archive_pipeline_decisions, cutoffs["pipeline_decisions"]),
        ("pipeline_stage_decisions", _purge_pipeline_stage_decisions, cutoffs["pipeline_stage_decisions"]),
        ("signal_log", _purge_signal_log, cutoffs["signal_log"]),
        ("operational_events", _purge_operational_events, cutoffs["operational_events"]),
    ]

    for table_name, task_fn, cutoff in tasks:
        try:
            async with session_factory() as session:
                count = await task_fn(session, cutoff)
                await session.commit()
                summary[table_name] = count
                if count > 0:
                    logger.info(
                        "[archival] %s: processed %d rows (cutoff=%s)",
                        table_name, count, cutoff.date(),
                    )
        except Exception as exc:
            logger.error(
                "[archival] %s: archival failed — %s",
                table_name, exc, exc_info=True,
            )
            summary[table_name] = -1  # -1 = error

    logger.info(
        "[archival] Cycle complete: pipeline_decisions=%d archived, "
        "pipeline_stage_decisions=%d purged, signal_log=%d purged, "
        "operational_events=%d purged",
        summary.get("pipeline_decisions", 0),
        summary.get("pipeline_stage_decisions", 0),
        summary.get("signal_log", 0),
        summary.get("operational_events", 0),
    )
    return summary
