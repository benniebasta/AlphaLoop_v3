"""Tests for the institutional pre-trade control plane."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.execution.control_plane import InstitutionalControlPlane


def _signal() -> SimpleNamespace:
    return SimpleNamespace(
        direction="BUY",
        setup_type="pullback",
        generated_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
        entry_zone=(3100.0, 3102.0),
    )


@pytest.mark.asyncio
async def test_preflight_passes_projected_risk_to_cross_instance_guard():
    cross_risk = AsyncMock()
    cross_risk.can_open_trade = AsyncMock(return_value=(False, "Cross-instance heat cap"))
    plane = InstitutionalControlPlane(
        session_factory=None,
        cross_risk=cross_risk,
        dry_run=True,
    )

    approval = await plane.preflight(
        symbol="XAUUSD",
        instance_id="inst-1",
        signal=_signal(),
        sizing={"lots": 0.20, "risk_amount_usd": 250.0},
        account_balance=10_000.0,
        strategy_id="XAUUSD.v4",
    )

    assert not approval.approved
    assert "heat cap" in approval.reason.lower()
    cross_risk.can_open_trade.assert_awaited_once_with(
        10_000.0,
        additional_risk_usd=250.0,
    )


@pytest.mark.asyncio
async def test_preflight_fails_closed_live_when_journal_unavailable():
    plane = InstitutionalControlPlane(
        session_factory=None,
        cross_risk=None,
        dry_run=False,
    )

    approval = await plane.preflight(
        symbol="XAUUSD",
        instance_id="inst-1",
        signal=_signal(),
        sizing={"lots": 0.20, "risk_amount_usd": 150.0},
        account_balance=10_000.0,
        strategy_id="XAUUSD.v4",
    )

    assert not approval.approved
    assert approval.order_id
    assert "journal" in approval.reason.lower()


@pytest.mark.asyncio
async def test_preflight_allows_dry_run_without_journal():
    plane = InstitutionalControlPlane(
        session_factory=None,
        cross_risk=None,
        dry_run=True,
    )

    approval = await plane.preflight(
        symbol="XAUUSD",
        instance_id="inst-1",
        signal=_signal(),
        sizing={"lots": 0.20, "risk_amount_usd": 150.0},
        account_balance=10_000.0,
        strategy_id="XAUUSD.v4",
    )

    assert approval.approved
    assert approval.order_id
    assert approval.client_order_id
    assert not approval.order_intent_persisted


def test_client_order_id_normalizes_setup_aliases():
    continuation_signal = _signal()
    continuation_signal.setup_type = "continuation"
    alias_signal = _signal()
    alias_signal.setup_type = "momentum_expansion"

    canonical = InstitutionalControlPlane._build_client_order_id(
        symbol="XAUUSD",
        signal=continuation_signal,
        strategy_id="XAUUSD.v4",
    )
    alias = InstitutionalControlPlane._build_client_order_id(
        symbol="XAUUSD",
        signal=alias_signal,
        strategy_id="XAUUSD.v4",
    )

    assert canonical == alias
