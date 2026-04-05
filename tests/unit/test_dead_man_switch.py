"""Tests for dead-man's-switch."""

import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alphaloop.core.events import RiskLimitHit
from alphaloop.db.models.incident import IncidentRecord
from alphaloop.monitoring.dead_man_switch import DeadManSwitch


@pytest.fixture
def hb_path(tmp_path):
    return str(tmp_path / "heartbeat.json")


def _write_heartbeat(path, ts=None):
    if ts is None:
        ts = datetime.now(timezone.utc)
    with open(path, "w") as f:
        json.dump({"timestamp": ts.isoformat()}, f)


def test_status_no_file():
    dms = DeadManSwitch(heartbeat_path="nonexistent.json")
    status = dms.status
    assert not status["running"]
    assert status["heartbeat_staleness_sec"] is None


def test_status_fresh_heartbeat(hb_path):
    _write_heartbeat(hb_path)
    dms = DeadManSwitch(heartbeat_path=hb_path)
    status = dms.status
    assert status["heartbeat_staleness_sec"] is not None
    assert status["heartbeat_staleness_sec"] < 5


def test_status_stale_heartbeat(hb_path):
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=700)
    _write_heartbeat(hb_path, old_ts)
    dms = DeadManSwitch(heartbeat_path=hb_path, warning_threshold_sec=600)
    staleness = dms._get_staleness_sec()
    assert staleness is not None
    assert staleness >= 700


def test_emergency_not_triggered_by_default(hb_path):
    _write_heartbeat(hb_path)
    dms = DeadManSwitch(heartbeat_path=hb_path)
    assert not dms._emergency_triggered
    assert dms.status["alert_level"] == "ok"


def test_invalid_heartbeat_json(hb_path):
    with open(hb_path, "w") as f:
        f.write("not json")
    dms = DeadManSwitch(heartbeat_path=hb_path)
    assert dms._get_staleness_sec() is None


@pytest.mark.asyncio
async def test_send_alert_publishes_valid_risk_event():
    event_bus = AsyncMock()
    dms = DeadManSwitch(event_bus=event_bus)

    await dms._send_alert("stale heartbeat")

    event_bus.publish.assert_awaited_once()
    event = event_bus.publish.await_args.args[0]
    assert isinstance(event, RiskLimitHit)
    assert event.limit_type == "dead_man_switch"
    assert "stale heartbeat" in event.details


@pytest.mark.asyncio
async def test_emergency_records_incident(db_engine, hb_path):
    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)
    dms = DeadManSwitch(heartbeat_path=hb_path, session_factory=factory)

    await dms._trigger_emergency_close()

    async with factory() as session:
        incidents = list((await session.execute(select(IncidentRecord))).scalars())
    assert len(incidents) == 1
    assert incidents[0].incident_type == "dead_man_switch_triggered"
