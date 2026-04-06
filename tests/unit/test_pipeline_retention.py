from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alphaloop.db.models.pipeline import PipelineDecision, PipelineDecisionArchive
from alphaloop.supervision.pipeline_retention import PipelineJourneyRetentionService


@pytest_asyncio.fixture
async def retention_session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_archive_expired_decisions_moves_old_rows_and_preserves_journey(retention_session_factory):
    now = datetime(2026, 4, 5, tzinfo=timezone.utc)
    old_time = now - timedelta(days=31)
    fresh_time = now - timedelta(days=5)

    async with retention_session_factory() as session:
        session.add_all([
            PipelineDecision(
                occurred_at=old_time,
                symbol="XAUUSD",
                direction="BUY",
                allowed=False,
                blocked_by="risk_gate",
                block_reason="portfolio heat cap",
                tool_results={
                    "journey": {
                        "final_outcome": "rejected",
                        "rejection_reason": "portfolio heat cap",
                        "stages": [
                            {"stage": "market_gate", "status": "passed"},
                            {"stage": "risk_gate", "status": "blocked"},
                        ],
                    }
                },
                instance_id="test-1",
            ),
            PipelineDecision(
                occurred_at=fresh_time,
                symbol="XAUUSD",
                direction="SELL",
                allowed=True,
                blocked_by=None,
                block_reason=None,
                tool_results={"journey": {"final_outcome": "trade_opened", "stages": []}},
                instance_id="test-1",
            ),
        ])
        await session.commit()

    service = PipelineJourneyRetentionService(retention_session_factory)
    report = await service.archive_expired_decisions(now=now, retention_days=30)

    assert report.archived_count == 1
    assert report.purged_count == 1

    async with retention_session_factory() as session:
        hot_rows = list((await session.execute(select(PipelineDecision).order_by(PipelineDecision.id))).scalars())
        archive_rows = list((await session.execute(select(PipelineDecisionArchive))).scalars())

    assert len(hot_rows) == 1
    assert hot_rows[0].direction == "SELL"

    assert len(archive_rows) == 1
    assert archive_rows[0].direction == "BUY"
    assert archive_rows[0].tool_results["journey"]["stages"][-1]["stage"] == "risk_gate"


@pytest.mark.asyncio
async def test_archive_expired_decisions_is_noop_when_nothing_expired(retention_session_factory):
    now = datetime(2026, 4, 5, tzinfo=timezone.utc)

    async with retention_session_factory() as session:
        session.add(
            PipelineDecision(
                occurred_at=now - timedelta(days=2),
                symbol="XAUUSD",
                direction="BUY",
                allowed=True,
                instance_id="test-1",
            )
        )
        await session.commit()

    service = PipelineJourneyRetentionService(retention_session_factory)
    report = await service.archive_expired_decisions(now=now, retention_days=30)

    assert report.archived_count == 0
    assert report.purged_count == 0

    async with retention_session_factory() as session:
        hot_count = len(list((await session.execute(select(PipelineDecision))).scalars()))
        archive_count = len(list((await session.execute(select(PipelineDecisionArchive))).scalars()))

    assert hot_count == 1
    assert archive_count == 0


@pytest.mark.asyncio
async def test_archive_expired_decisions_skips_when_archive_table_missing():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(PipelineDecision.__table__.create)

        service = PipelineJourneyRetentionService(factory)
        report = await service.archive_expired_decisions(
            now=datetime(2026, 4, 5, tzinfo=timezone.utc),
            retention_days=30,
        )

        assert report.skipped is True
        assert report.skip_reason == "required pipeline retention tables missing"
        assert report.archived_count == 0
        assert report.purged_count == 0
    finally:
        await engine.dispose()
