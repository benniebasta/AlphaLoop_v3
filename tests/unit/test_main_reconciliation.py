"""Tests for reconciliation fail-safe helpers in alphaloop.main."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.main import (
    _reconciliation_payload,
    _run_pipeline_retention_cycle,
    _trip_reconciliation_kill_switch,
)
from alphaloop.risk.monitor import RiskMonitor


def test_reconciliation_payload_serializes_report():
    """Reconciliation payload should preserve report fields for incidents."""
    issue = SimpleNamespace(
        ticket=12345,
        symbol="XAUUSD",
        issue_type="orphaned_broker_position",
        description="Broker position missing DB trade",
        severity="critical",
        auto_resolved=False,
    )
    report = SimpleNamespace(
        reconciled=False,
        has_critical=True,
        issue_count=1,
        broker_positions=1,
        db_open_trades=0,
        issues=[issue],
    )

    payload = _reconciliation_payload(report, stage="background")

    assert payload["stage"] == "background"
    assert payload["has_critical"] is True
    assert payload["issue_count"] == 1
    assert payload["issues"][0]["ticket"] == 12345
    assert payload["issues"][0]["issue_type"] == "orphaned_broker_position"


@pytest.mark.asyncio
async def test_trip_reconciliation_kill_switch_activates_monitor_and_records_incident():
    """Critical reconciliation issues must fail closed via the kill switch."""
    monitor = RiskMonitor(10_000.0)
    monitor._seeded = True
    record_incident = AsyncMock()

    await _trip_reconciliation_kill_switch(
        monitor,
        "Background reconciliation found critical issues",
        record_incident=record_incident,
        incident_type="bg_reconciliation_critical",
        details="Background reconciliation found 2 critical issues",
        payload={"stage": "background", "issue_count": 2},
    )

    assert monitor.kill_switch_active is True
    assert monitor.no_new_risk_active is True
    assert monitor.no_new_risk_reasons == ("bg_reconciliation_critical",)
    assert monitor.force_close_all is True
    record_incident.assert_awaited_once_with(
        "bg_reconciliation_critical",
        "Background reconciliation found 2 critical issues",
        severity="critical",
        payload={"stage": "background", "issue_count": 2},
    )


@pytest.mark.asyncio
async def test_trip_reconciliation_kill_switch_records_incident_without_monitor():
    """Incident recording should still work if the monitor is unavailable."""
    record_incident = AsyncMock()

    await _trip_reconciliation_kill_switch(
        None,
        "Background reconciliation failed: broker timeout",
        record_incident=record_incident,
        incident_type="bg_reconciliation_failure",
        details="Background reconciliation failed: broker timeout",
        payload={"stage": "background"},
    )

    record_incident.assert_awaited_once_with(
        "bg_reconciliation_failure",
        "Background reconciliation failed: broker timeout",
        severity="critical",
        payload={"stage": "background"},
    )


@pytest.mark.asyncio
async def test_trip_reconciliation_kill_switch_uses_explicit_no_new_risk_reason():
    """Explicit no-new-risk reasons should be tracked separately from incidents."""
    monitor = RiskMonitor(10_000.0)
    monitor._seeded = True
    record_incident = AsyncMock()

    await _trip_reconciliation_kill_switch(
        monitor,
        "Background reconciliation failed: broker timeout",
        record_incident=record_incident,
        incident_type="bg_reconciliation_failure",
        details="Background reconciliation failed: broker timeout",
        no_new_risk_reason="reconciler_failure",
        payload={"stage": "background"},
    )

    assert monitor.kill_switch_active is True
    assert monitor.no_new_risk_reasons == ("reconciler_failure",)


@pytest.mark.asyncio
async def test_run_pipeline_retention_cycle_records_archived_counts():
    report = SimpleNamespace(
        cutoff=SimpleNamespace(astimezone=lambda _tz: SimpleNamespace(isoformat=lambda: "2026-03-06T00:00:00+00:00")),
        archived_count=3,
        purged_count=3,
        skipped=False,
        skip_reason=None,
    )
    fake_service = SimpleNamespace(
        archive_expired_decisions=AsyncMock(return_value=report)
    )
    record_event = AsyncMock()

    payload = await _run_pipeline_retention_cycle(
        session_factory=None,
        retention_days=30,
        batch_size=500,
        record_event=record_event,
        retention_service=fake_service,
    )

    assert payload["archived_count"] == 3
    assert payload["purged_count"] == 3
    assert payload["skipped"] is False
    record_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_pipeline_retention_cycle_records_skip_reason():
    report = SimpleNamespace(
        cutoff=SimpleNamespace(astimezone=lambda _tz: SimpleNamespace(isoformat=lambda: "2026-03-06T00:00:00+00:00")),
        archived_count=0,
        purged_count=0,
        skipped=True,
        skip_reason="required pipeline retention tables missing",
    )
    fake_service = SimpleNamespace(
        archive_expired_decisions=AsyncMock(return_value=report)
    )
    record_event = AsyncMock()

    payload = await _run_pipeline_retention_cycle(
        session_factory=None,
        record_event=record_event,
        retention_service=fake_service,
    )

    assert payload["skipped"] is True
    assert payload["skip_reason"] == "required pipeline retention tables missing"
    record_event.assert_awaited_once()
