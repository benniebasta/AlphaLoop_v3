from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alphaloop.db.models.incident import IncidentRecord
from alphaloop.monitoring.watchdog import TradingWatchdog


@pytest.mark.asyncio
async def test_watchdog_alert_records_incident(db_engine):
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    watchdog = TradingWatchdog(session_factory=factory)

    await watchdog._alert("critical", "Trading loop UNRESPONSIVE", {"age_seconds": 901})

    async with factory() as session:
        incidents = list((await session.execute(select(IncidentRecord))).scalars())

    assert len(incidents) == 1
    assert incidents[0].incident_type == "watchdog_triggered"
    assert incidents[0].severity == "critical"
