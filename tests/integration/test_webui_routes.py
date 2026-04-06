"""Integration tests for all WebUI API routes.

Uses httpx.AsyncClient with ASGITransport against the real FastAPI app
backed by an in-memory SQLite database (via the shared ``container`` fixture).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import alphaloop.backtester.runner as bt_runner_module
import alphaloop.seedlab.background_runner as seedlab_runner_module
import alphaloop.webui.routes.bots as bots_route
import alphaloop.webui.routes.event_log as event_log_route
import alphaloop.webui.routes.strategies as strategies_route
from alphaloop.db.models.backtest import BacktestRun
from alphaloop.db.models.incident import IncidentRecord
from alphaloop.db.models.operational_event import OperationalEvent
from alphaloop.db.models.order import OrderRecord
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.db.models.pipeline import PipelineDecision
from alphaloop.db.models.research import ResearchReport
from alphaloop.db.models.trade import TradeLog
from alphaloop.db.models.instance import RunningInstance
from alphaloop.db.repositories.backtest_repo import BacktestRepository
from alphaloop.webui.app import create_webui_app


_TEST_AUTH_TOKEN = "test-auth-token-for-integration"


@pytest_asyncio.fixture
async def client(container, monkeypatch):
    """Async HTTP client wired to the ASGI app with in-memory DB.

    Sets AUTH_TOKEN so Phase 7E auth enforcement passes for sensitive endpoints.
    """
    monkeypatch.setenv("AUTH_TOKEN", _TEST_AUTH_TOKEN)
    app = create_webui_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_TEST_AUTH_TOKEN}"},
    ) as c:
        yield c


# ── Health ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert data["status"] == "ok"


# ── Dashboard ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard(client):
    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    for key in (
        "open_trades",
        "daily_pnl",
        "daily_trades",
        "daily_win_rate",
        "weekly_pnl",
        "total_pnl",
        "total_trades",
        "overall_win_rate",
    ):
        assert key in data, f"Missing key: {key}"


@pytest.mark.asyncio
async def test_live_route_surfaces_db_backed_pipeline_and_event_telemetry(client, container):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(
            PipelineDecision(
                symbol="XAUUSD",
                direction="BUY",
                allowed=False,
                blocked_by="risk_gate",
                block_reason="portfolio heat cap",
                tool_results={
                    "journey": {
                        "final_outcome": "rejected",
                        "stages": [
                            {"stage": "market_gate", "status": "passed"},
                            {"stage": "risk_gate", "status": "blocked"},
                        ],
                    },
                    "construction_source": "swing_low",
                },
                instance_id="live-test-1",
                occurred_at=now,
            )
        )
        session.add(
            OperationalEvent(
                category="execution",
                event_type="trade_opened",
                severity="info",
                symbol="XAUUSD",
                instance_id="live-test-1",
                message="Opened dry-run position",
                payload={"ticket": "dry-1"},
                created_at=now,
            )
        )
        await session.commit()

    resp = await client.get("/api/live?symbol=XAUUSD&timeframe=1m")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pipeline_status"]) >= 1
    assert data["pipeline_status"][0]["blocked_by"] == "risk_gate"
    assert data["pipeline_status"][0]["journey"]["final_outcome"] == "rejected"
    assert data["pipeline_status"][0]["construction_source"] == "swing_low"
    assert len(data["agent_thoughts"]) >= 1
    assert data["agent_thoughts"][0]["event_type"] == "trade_opened"
    assert data["agent_thoughts"][0]["payload"]["ticket"] == "dry-1"


# ── Trades ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trades(client):
    resp = await client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert "trades" in data
    assert isinstance(data["trades"], list)


@pytest.mark.asyncio
async def test_execution_ledger_and_reconciliation_routes(client, container):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(OrderRecord(
            order_id="ord-1",
            client_order_id="cid-1",
            symbol="XAUUSD",
            direction="BUY",
            lots=0.2,
            status="APPROVED",
            requested_price=2310.5,
            instance_id="test-instance",
            transitions=[{"to_status": "PENDING"}, {"to_status": "APPROVED"}],
        ))
        session.add(OperationalEvent(
            category="reconciliation",
            event_type="startup_reconciliation_completed",
            severity="warning",
            symbol="XAUUSD",
            instance_id="test-instance",
            message="Startup reconciliation completed",
            payload={"issue_count": 1, "has_critical": False},
            created_at=now,
        ))
        await session.commit()

    orders_resp = await client.get("/api/execution/orders")
    assert orders_resp.status_code == 200
    orders_data = orders_resp.json()
    assert orders_data["count"] >= 1
    assert orders_data["orders"][0]["client_order_id"] == "cid-1"

    recon_resp = await client.get("/api/execution/reconcile")
    assert recon_resp.status_code == 200
    recon_data = recon_resp.json()
    assert recon_data["latest_report"]["event_type"] == "startup_reconciliation_completed"
    assert recon_data["unresolved_count"] >= 1


@pytest.mark.asyncio
async def test_execution_backfill_requires_operator_auth(client):
    resp = await client.post(
        "/api/execution/attribution/backfill",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_execution_backfill_writes_operator_audit(client, container, monkeypatch):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(TradeLog(
            signal_id="sig-backfill",
            client_order_id="cid-backfill",
            symbol="XAUUSD",
            direction="BUY",
            setup_type="trend",
            entry_price=2300.0,
            entry_zone_low=2299.0,
            entry_zone_high=2301.0,
            stop_loss=2290.0,
            take_profit_1=2320.0,
            lot_size=0.3,
            risk_pct=1.0,
            risk_amount_usd=120.0,
            outcome="WIN",
            opened_at=now,
            closed_at=now,
            close_price=2310.0,
            pnl_usd=75.0,
            order_ticket=201,
            instance_id="test-instance",
            is_dry_run=False,
        ))
        await session.commit()

    import alphaloop.research.attribution as attribution_module

    monkeypatch.setattr(
        attribution_module.TradeAttributor,
        "compute_attribution",
        lambda self, trade: {
            "pnl_entry_skill": 12.5,
            "pnl_exit_skill": 0.8,
            "pnl_slippage_usd": -1.0,
            "pnl_commission_usd": -0.5,
        },
    )

    resp = await client.post("/api/execution/attribution/backfill?limit=10")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["updated"] == 1

    async with container.db_session_factory() as session:
        trade = (await session.execute(
            select(TradeLog).where(TradeLog.client_order_id == "cid-backfill")
        )).scalar_one()
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action == "execution_attribution_backfill"
            )
        )).scalars())
    assert trade.pnl_entry_skill == 12.5
    assert trade.pnl_exit_skill == 0.8
    assert len(audit_rows) >= 1
    assert audit_rows[-1].target == "trade_logs"


@pytest.mark.asyncio
async def test_portfolio_and_incident_routes(client, container):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(TradeLog(
            signal_id="sig-open",
            client_order_id="cid-open",
            symbol="XAUUSD",
            direction="BUY",
            setup_type="trend",
            entry_price=2300.0,
            entry_zone_low=2299.0,
            entry_zone_high=2301.0,
            stop_loss=2290.0,
            take_profit_1=2320.0,
            lot_size=0.3,
            risk_pct=1.0,
            risk_amount_usd=120.0,
            outcome="OPEN",
            opened_at=now,
            order_ticket=101,
            instance_id="test-instance",
            is_dry_run=False,
        ))
        session.add(TradeLog(
            signal_id="sig-closed",
            client_order_id="cid-closed",
            symbol="XAUUSD",
            direction="SELL",
            setup_type="reversal",
            entry_price=2310.0,
            stop_loss=2320.0,
            take_profit_1=2290.0,
            lot_size=0.2,
            risk_pct=1.0,
            risk_amount_usd=80.0,
            outcome="WIN",
            opened_at=now,
            closed_at=now,
            pnl_usd=75.0,
            order_ticket=102,
            instance_id="test-instance",
            is_dry_run=False,
        ))
        session.add(IncidentRecord(
            incident_type="reconciliation_block",
            status="OPEN",
            severity="critical",
            title="Reconciliation Block",
            details="Startup reconciliation found unresolved issues",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "startup"},
        ))
        await session.commit()

    portfolio_resp = await client.get("/api/risk/portfolio")
    assert portfolio_resp.status_code == 200
    portfolio = portfolio_resp.json()
    assert portfolio["gross_risk_usd"] == 120.0
    assert portfolio["open_positions"] == 1
    assert portfolio["guard_state"]["no_new_risk_active"] is False

    incidents_resp = await client.get("/api/controls/incidents")
    assert incidents_resp.status_code == 200
    incidents_data = incidents_resp.json()
    assert incidents_data["count"] >= 1
    incident_id = incidents_data["incidents"][0]["id"]

    ack_resp = await client.post(
        f"/api/controls/incidents/{incident_id}/ack",
        json={"operator": "tester", "note": "acknowledged in integration test"},
    )
    assert ack_resp.status_code == 200
    assert ack_resp.json()["incident"]["status"] == "ACKNOWLEDGED"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "incident_acknowledge")
        )).scalars())
    assert len(audit_rows) == 1
    assert audit_rows[0].operator == "tester"
    assert audit_rows[0].target == str(incident_id)


@pytest.mark.asyncio
async def test_acknowledge_incident_requires_operator_auth(client, container):
    async with container.db_session_factory() as session:
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_failure",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Failure",
            details="Background reconciliation failed",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        await session.commit()
        result = await session.execute(select(IncidentRecord.id).order_by(IncidentRecord.id.desc()))
        incident_id = result.scalar_one()

    resp = await client.post(
        f"/api/controls/incidents/{incident_id}/ack",
        json={"operator": "tester", "note": "missing auth should fail"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_risk_state_route_surfaces_active_no_new_risk_reasons(client, container):
    async with container.db_session_factory() as session:
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_failure",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Failure",
            details="Background reconciliation failed",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_critical",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Critical",
            details="Background reconciliation found critical issues",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        await session.commit()

    resp = await client.get("/api/controls/risk-state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["no_new_risk_active"] is True
    assert data["active_reasons"] == [
        "broker_db_split_brain",
        "reconciler_failure",
    ]
    assert data["compound_clearable"] is False
    assert "all active reasons" in data["clear_rule"]
    assert len(data["reason_incident_ids"]["broker_db_split_brain"]) == 1
    assert len(data["reason_incident_ids"]["reconciler_failure"]) == 1
    assert data["reason_details"]["broker_db_split_brain"]["clearable"] is False
    assert (
        data["reason_details"]["broker_db_split_brain"]["clear_prerequisite"]
        == "recovery tombstones resolved and reconciler clean"
    )
    assert (
        data["reason_details"]["reconciler_failure"]["clear_prerequisite"]
        == "reconciler clean state restored"
    )

    portfolio_resp = await client.get("/api/risk/portfolio")
    assert portfolio_resp.status_code == 200
    portfolio = portfolio_resp.json()
    assert portfolio["guard_state"]["no_new_risk_active"] is True
    assert "reconciler_failure" in portfolio["guard_state"]["active_reasons"]
    assert portfolio["guard_state"]["compound_clearable"] is False


# ── Settings ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acknowledged_incident_remains_active_in_risk_state(client, container):
    async with container.db_session_factory() as session:
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_failure",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Failure",
            details="Background reconciliation failed",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        await session.commit()
        result = await session.execute(select(IncidentRecord.id).order_by(IncidentRecord.id.desc()))
        incident_id = result.scalar_one()

    ack_resp = await client.post(
        f"/api/controls/incidents/{incident_id}/ack",
        json={"operator": "tester", "note": "verified and still active"},
    )
    assert ack_resp.status_code == 200

    risk_resp = await client.get("/api/controls/risk-state")
    assert risk_resp.status_code == 200
    data = risk_resp.json()
    assert data["no_new_risk_active"] is True
    assert data["active_reasons"] == ["reconciler_failure"]
    assert data["reason_details"]["reconciler_failure"]["clearable"] is True
    assert data["compound_clearable"] is True


@pytest.mark.asyncio
async def test_clear_no_new_risk_requires_all_active_reasons_acknowledged(client, container):
    async with container.db_session_factory() as session:
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_failure",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Failure",
            details="Background reconciliation failed",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_critical",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Critical",
            details="Background reconciliation found critical issues",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        await session.commit()
        result = await session.execute(select(IncidentRecord.id).order_by(IncidentRecord.id.asc()))
        incident_ids = list(result.scalars())[-2:]

    ack_resp = await client.post(
        f"/api/controls/incidents/{incident_ids[0]}/ack",
        json={"operator": "tester", "note": "first reason acknowledged"},
    )
    assert ack_resp.status_code == 200

    clear_resp = await client.post(
        "/api/controls/no-new-risk/clear",
        json={"operator": "tester", "note": "should fail until all reasons acknowledged"},
    )
    assert clear_resp.status_code == 409
    detail = clear_resp.json()["detail"]
    assert "All active no_new_risk reasons" in detail["message"]
    assert detail["risk_state"]["no_new_risk_active"] is True
    assert detail["risk_state"]["compound_clearable"] is False


@pytest.mark.asyncio
async def test_clear_no_new_risk_resolves_all_active_reasons_and_audits(client, container):
    async with container.db_session_factory() as session:
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_failure",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Failure",
            details="Background reconciliation failed",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_critical",
            status="OPEN",
            severity="critical",
            title="Background Reconciliation Critical",
            details="Background reconciliation found critical issues",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        await session.commit()
        result = await session.execute(select(IncidentRecord.id).order_by(IncidentRecord.id.asc()))
        incident_ids = list(result.scalars())[-2:]

    for incident_id in incident_ids:
        ack_resp = await client.post(
            f"/api/controls/incidents/{incident_id}/ack",
            json={"operator": "tester", "note": "ready to clear"},
        )
        assert ack_resp.status_code == 200

    clear_resp = await client.post(
        "/api/controls/no-new-risk/clear",
        json={"operator": "tester", "note": "all active reasons resolved"},
    )
    assert clear_resp.status_code == 200
    data = clear_resp.json()
    assert data["cleared"] is True
    assert sorted(data["resolved_incident_ids"]) == sorted(incident_ids)
    assert data["risk_state"]["no_new_risk_active"] is False

    async with container.db_session_factory() as session:
        incidents = list((await session.execute(
            select(IncidentRecord).where(IncidentRecord.id.in_(incident_ids))
        )).scalars())
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "no_new_risk_clear")
        )).scalars())
    assert all(incident.status == "RESOLVED" for incident in incidents)
    assert len(audit_rows) >= 1
    assert audit_rows[-1].new_value == "RESOLVED"


@pytest.mark.asyncio
async def test_clear_no_new_risk_requires_operator_auth(client, container):
    async with container.db_session_factory() as session:
        session.add(IncidentRecord(
            incident_type="bg_reconciliation_failure",
            status="ACKNOWLEDGED",
            severity="critical",
            title="Background Reconciliation Failure",
            details="Background reconciliation failed",
            symbol="XAUUSD",
            instance_id="test-instance",
            source="test",
            payload={"stage": "background"},
        ))
        await session.commit()

    resp = await client.post(
        "/api/controls/no-new-risk/clear",
        json={"operator": "tester", "note": "missing auth should fail"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_settings(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "settings" in data


@pytest.mark.asyncio
async def test_put_settings(client):
    resp = await client.put(
        "/api/settings",
        json={"settings": {"TEST_KEY": "value"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"
    assert "TEST_KEY" in data.get("updated", [])


@pytest.mark.asyncio
async def test_put_settings_writes_operator_audit(client, container):
    resp = await client.put(
        "/api/settings",
        json={"settings": {"UI_THEME": "value", "API_TOKEN": "secret-token-value"}},
    )
    assert resp.status_code == 200

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "settings_update")
        )).scalars())

    assert len(audit_rows) >= 2
    by_target = {row.target: row for row in audit_rows}
    assert by_target["UI_THEME"].new_value == "value"
    assert by_target["API_TOKEN"].new_value == "***"


@pytest.mark.asyncio
async def test_put_settings_rejects_unsafe_risk_values(client):
    resp = await client.put(
        "/api/settings",
        json={"settings": {"MAX_DAILY_LOSS_PCT": "0.50"}},
    )

    assert resp.status_code == 422
    assert "MAX_DAILY_LOSS_PCT" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_put_settings_requires_operator_auth(client):
    resp = await client.put(
        "/api/settings",
        json={"settings": {"TEST_KEY": "value"}},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


# ── Backtests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backtests(client):
    resp = await client.get("/api/backtests")
    assert resp.status_code == 200
    data = resp.json()
    assert "backtests" in data
    assert isinstance(data["backtests"], list)


@pytest.mark.asyncio
async def test_backtests_list_prefers_strategy_spec_signal_mode_in_plan(client, container):
    async with container.db_session_factory() as session:
        session.add(
            BacktestRun(
                run_id="bt-spec-mode-1",
                symbol="XAUUSD",
                name="Spec-aware Backtest",
                plan=json.dumps(
                    {
                        "signal_mode": "algo_ai",
                        "strategy_spec": {
                            "signal_mode": "algo_only",
                            "setup_family": "momentum_expansion",
                        },
                        "signal_rules": [{"source": "ema_crossover"}],
                        "signal_logic": "AND",
                        "signal_auto": False,
                    }
                ),
                state="completed",
                days=30,
                timeframe="15m",
                balance=10000.0,
                max_generations=3,
            )
        )
        await session.commit()

    resp = await client.get("/api/backtests")
    assert resp.status_code == 200
    items = {item["run_id"]: item for item in resp.json()["backtests"]}
    assert items["bt-spec-mode-1"]["signal_mode"] == "algo_only"
    assert items["bt-spec-mode-1"]["setup_family"] == "momentum_expansion"


@pytest.mark.asyncio
async def test_backtests_symbols(client):
    resp = await client.get("/api/backtests/symbols")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_backtest_requires_operator_auth(client):
    resp = await client.post(
        "/api/backtests",
        json={"symbol": "XAUUSD"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_backtest_create_and_delete_write_operator_audit(client, container, monkeypatch):
    async def _fake_start_backtest(**kwargs):
        return None

    monkeypatch.setattr(bt_runner_module, "start_backtest", _fake_start_backtest)
    monkeypatch.setattr(bt_runner_module, "delete_run_data", lambda run_id: None)

    create_resp = await client.post(
        "/api/backtests",
        json={
            "symbol": "XAUUSD",
            "days": 30,
            "balance": 5000.0,
            "max_generations": 1,
            "use_bos_guard": True,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["backtest"]
    run_id = created["run_id"]
    assert created["setup_family"] == "breakout_retest"
    assert created["source"] == "backtest_runner"
    async with container.db_session_factory() as session:
        stored_run = await session.get(BacktestRun, created["id"])
    assert stored_run is not None
    assert {k for k, v in (stored_run.tools_json or {}).items() if v} == set(created["tools"])

    delete_resp = await client.delete(f"/api/backtests/{run_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] == run_id

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action.in_(["backtest_create", "backtest_delete"])
            )
        )).scalars())
    actions = {row.action for row in audit_rows if row.target == run_id}
    assert "backtest_create" in actions
    assert "backtest_delete" in actions
    create_audit = next(row for row in audit_rows if row.target == run_id and row.action == "backtest_create")
    assert '"setup_family": "breakout_retest"' in create_audit.new_value
    assert '"source": "backtest_runner"' in create_audit.new_value


@pytest.mark.asyncio
async def test_backtest_create_preserves_explicit_empty_signal_rules(client, monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_start_backtest(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(bt_runner_module, "start_backtest", _fake_start_backtest)

    create_resp = await client.post(
        "/api/backtests",
        json={
            "symbol": "XAUUSD",
            "days": 30,
            "balance": 5000.0,
            "max_generations": 1,
            "signal_rules": [],
            "signal_logic": "weird",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["backtest"]

    assert created["signal_rules"] == []
    assert created["signal_logic"] == "AND"
    assert captured["signal_rules"] == []
    assert captured["signal_logic"] == "AND"


@pytest.mark.asyncio
async def test_backtest_create_passes_spec_first_plan_metadata_to_runner(client, monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_start_backtest(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(bt_runner_module, "start_backtest", _fake_start_backtest)

    create_resp = await client.post(
        "/api/backtests",
        json={
            "symbol": "XAUUSD",
            "days": 30,
            "balance": 5000.0,
            "max_generations": 1,
            "signal_mode": "algo_ai",
            "signal_rules": [{"source": "ema_crossover"}],
            "use_bos_guard": True,
        },
    )
    assert create_resp.status_code == 200
    assert captured["setup_family"] == "breakout_retest"
    assert captured["source"] == "backtest_runner"
    assert captured["strategy_spec"]["setup_family"] == "breakout_retest"


@pytest.mark.asyncio
async def test_backtest_resume_prefers_plan_tools_over_stale_tools_json(client, container, monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_start_backtest(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(bt_runner_module, "start_backtest", _fake_start_backtest)
    monkeypatch.setattr(bt_runner_module, "is_running", lambda run_id: False)

    async with container.db_session_factory() as session:
        repo = BacktestRepository(session)
        run = await repo.create(
            run_id="resume-tools-1",
            symbol="XAUUSD",
            name="resume-tools",
            plan=json.dumps(
                {
                    "signal_mode": "algo_ai",
                    "setup_family": "momentum_expansion",
                    "source": "backtest_runner",
                    "signal_rules": [{"source": "ema_crossover"}],
                    "signal_logic": "AND",
                    "signal_auto": False,
                    "tools": {"fast_fingers": True, "bos_guard": False},
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "algo_ai",
                        "setup_family": "momentum_expansion",
                    },
                }
            ),
            days=30,
            timeframe="1h",
            balance=5000.0,
            max_generations=1,
            tools_json=["bos_guard"],
            state="paused",
        )
        await session.commit()
        run_id = run.run_id

    resp = await client.patch(f"/api/backtests/{run_id}/resume")
    assert resp.status_code == 200
    assert captured["tools"] == ["fast_fingers"]
    assert captured["setup_family"] == "momentum_expansion"
    assert captured["strategy_spec"]["setup_family"] == "momentum_expansion"


@pytest.mark.asyncio
async def test_create_seedlab_requires_operator_auth(client):
    resp = await client.post(
        "/api/seedlab",
        json={"name": "seed-run", "symbol": "XAUUSD"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_seedlab_create_and_delete_write_operator_audit(client, container, monkeypatch):
    async def _fake_start_seedlab_run(**kwargs):
        return None

    monkeypatch.setattr(seedlab_runner_module, "start_seedlab_run", _fake_start_seedlab_run)
    monkeypatch.setattr(seedlab_runner_module, "delete_run_data", lambda run_id: None)

    create_resp = await client.post(
        "/api/seedlab",
        json={"name": "seed-run", "symbol": "XAUUSD", "days": 30, "balance": 5000.0},
    )
    assert create_resp.status_code == 200
    run_id = create_resp.json()["run_id"]

    delete_resp = await client.delete(f"/api/seedlab/{run_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["run_id"] == run_id

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action.in_(["seedlab_create", "seedlab_delete"])
            )
        )).scalars())
    actions = {row.action for row in audit_rows if row.target == run_id}
    assert "seedlab_create" in actions
    assert "seedlab_delete" in actions


@pytest.mark.asyncio
async def test_mt5_symbols(client, monkeypatch):
    class _FakeSymbol:
        def __init__(self, name, description, visible=True, selected=True, path="Market\\Forex"):
            self.name = name
            self.description = description
            self.visible = visible
            self.select = selected
            self.path = path

    class _FakeMT5:
        def initialize(self, **kwargs):
            self.kwargs = kwargs
            return True

        def last_error(self):
            return (0, "OK")

        def symbols_get(self):
            return [
                _FakeSymbol("XAUUSDm", "Gold"),
                _FakeSymbol("EURUSD", "Euro / US Dollar", visible=False),
            ]

        def shutdown(self):
            return None

    monkeypatch.setitem(sys.modules, "MetaTrader5", _FakeMT5())
    await client.put(
        "/api/settings",
        json={
            "settings": {
                "MT5_SERVER": "demo-server",
                "MT5_LOGIN": "123456",
                "MT5_PASSWORD": "plain-password",
            }
        },
    )

    resp = await client.get("/api/test/mt5/symbols")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["source"] == "mt5"
    assert any(item["symbol"] == "XAUUSDm" for item in data["symbols"])


@pytest.mark.asyncio
async def test_ai_key_connection_requires_operator_auth(client):
    resp = await client.post(
        "/api/test/ai-key",
        json={"provider": "gemini", "model": "gemini-2.5-flash"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ai_key_connection_writes_operator_audit(client, container, monkeypatch):
    async with container.db_session_factory() as session:
        from alphaloop.db.repositories.settings_repo import SettingsRepository
        repo = SettingsRepository(session)
        await repo.set("GEMINI_API_KEY", "plain-test-key")
        await session.commit()

    class _FakeCaller:
        def __init__(self, api_keys=None):
            self.api_keys = api_keys or {}

        async def call_model(self, model, **kwargs):
            return "OK"

    import alphaloop.ai.caller as ai_caller_module
    monkeypatch.setattr(ai_caller_module, "AICaller", _FakeCaller)

    resp = await client.post(
        "/api/test/ai-key",
        json={"provider": "gemini", "model": "gemini-2.5-flash"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "connection_test")
        )).scalars())
    assert any(row.target == "ai-key:gemini" for row in audit_rows)


@pytest.mark.asyncio
async def test_ollama_connection_requires_operator_auth(client):
    resp = await client.post(
        "/api/test/ollama",
        json={"base_url": "http://localhost:11434/v1"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ollama_connection_writes_operator_audit(client, container, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"models": [{"name": "qwen2.5:latest"}, {"name": "llama3.1:8b"}]}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    resp = await client.post(
        "/api/test/ollama",
        json={"base_url": "http://localhost:11434/v1"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "connection_test")
        )).scalars())
    assert any(row.target == "ollama" for row in audit_rows)


@pytest.mark.asyncio
async def test_news_connection_requires_operator_auth(client):
    resp = await client.post(
        "/api/test/news",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_news_connection_writes_operator_audit(client, container, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [
                {"impact": "high"},
                {"impact": "medium"},
            ]

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    resp = await client.post("/api/test/news")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "connection_test")
        )).scalars())
    assert any(row.target == "news:forexfactory" for row in audit_rows)


# ── Strategies ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_signal_discovery_mode_split(client, container):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Discovery Card",
            "signal_instruction": "Find strong momentum continuation setups.",
            "validator_instruction": "Reject weak setups.",
            "source": "ai_signal_discovery",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]
    assert created["signal_mode"] == "ai_signal"
    assert created["spec_version"] == "v1"
    assert created["strategy_spec"]["spec_version"] == "v1"
    assert created["strategy_spec"]["setup_family"] == "discretionary_ai"
    assert created["source"] == "ai_signal_discovery"
    assert created["params"]["signal_rules"] == []
    assert created["strategy_spec"]["entry_model"]["signal_rule_sources"] == []

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_create")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)

    blocked_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        json={
            "signal_mode": "ai_signal",
            "source": "strategies",
        },
    )
    assert blocked_resp.status_code == 400

    allowed_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        json={
            "signal_mode": "ai_signal",
            "source": "ai_signal_discovery",
            "name": "Discovery Card Updated",
        },
    )
    assert allowed_resp.status_code == 200
    updated = allowed_resp.json()["strategy"]
    assert updated["signal_mode"] == "ai_signal"
    assert updated["source"] == "ai_signal_discovery"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_update")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)

    list_resp = await client.get("/api/strategies?signal_mode=algo_only,algo_ai")
    assert list_resp.status_code == 200
    assert all(item.get("signal_mode") != "ai_signal" for item in list_resp.json()["strategies"])


@pytest.mark.asyncio
async def test_ai_signal_card_source_override(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Strategy Menu Card",
            "signal_instruction": "Prefer high-conviction trend continuation only.",
            "validator_instruction": "Reject marginal setups.",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]
    assert created["signal_mode"] == "ai_signal"
    assert created["spec_version"] == "v1"
    assert created["source"] == "ui_ai_signal_card"


@pytest.mark.asyncio
async def test_strategy_list_filter_uses_effective_spec_signal_mode(client, monkeypatch):
    monkeypatch.setattr(
        strategies_route,
        "_load_all_versions",
        lambda: [
            {
                "symbol": "XAUUSD",
                "version": 1,
                "status": "candidate",
                "signal_mode": "algo_only",
                "strategy_spec": {
                    "spec_version": "v1",
                    "signal_mode": "ai_signal",
                    "setup_family": "discretionary_ai",
                    "prompt_bundle": {},
                },
            },
            {
                "symbol": "XAUUSD",
                "version": 2,
                "status": "candidate",
                "signal_mode": "algo_only",
                "strategy_spec": {
                    "spec_version": "v1",
                    "signal_mode": "algo_only",
                    "setup_family": "trend_continuation",
                    "prompt_bundle": {},
                },
            },
        ],
    )

    resp = await client.get("/api/strategies?signal_mode=algo_only")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["strategies"][0]["version"] == 2


@pytest.mark.asyncio
async def test_strategy_evaluate_promotion_uses_effective_spec_signal_mode_for_gate(client, monkeypatch):
    monkeypatch.setattr(
        strategies_route,
        "_load_version",
        lambda symbol, version: {
            "symbol": symbol,
            "version": version,
            "status": "candidate",
            "source": "ui_ai_signal_card",
            "signal_mode": "algo_only",
            "summary": {},
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        },
    )

    async def _fake_get(self, key, default=None):
        values = {
            "PROMOTION_CANDIDATE_GATE_AI_SIGNAL": "false",
            "PROMOTION_CANDIDATE_GATE_ALGO_ONLY": "true",
        }
        return values.get(key, default)

    captured: dict[str, object] = {}

    async def _fake_evaluate_promotion(self, *, current_status, metrics, cycles_completed, bypass_candidate_gate):
        captured["bypass_candidate_gate"] = bypass_candidate_gate
        return {
            "eligible": True,
            "promoted": False,
            "next_status": "dry_run",
            "reason": "captured in test",
        }

    monkeypatch.setattr(strategies_route.SettingsRepository, "get", _fake_get)

    import alphaloop.backtester.deployment_pipeline as deployment_pipeline_module

    monkeypatch.setattr(
        deployment_pipeline_module.DeploymentPipeline,
        "evaluate_promotion",
        _fake_evaluate_promotion,
    )

    resp = await client.post(
        "/api/strategies/XAUUSD/v7/evaluate",
        json={"cycles_completed": 3},
    )

    assert resp.status_code == 200
    assert captured["bypass_candidate_gate"] is True


@pytest.mark.asyncio
async def test_create_ai_signal_card_requires_operator_auth(client):
    resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Unauthorized Discovery Card",
            "source": "ai_signal_discovery",
        },
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_strategy_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Auth Update Guard Card",
            "source": "ai_signal_discovery",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        json={"name": "Should Fail"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_strategies(client):
    resp = await client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert "strategies" in data
    assert isinstance(data["strategies"], list)


@pytest.mark.asyncio
async def test_activate_strategy_writes_operator_audit(client, container):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Activation Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    update_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        json={"status": "dry_run"},
    )
    assert update_resp.status_code == 200

    activate_resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/activate",
    )
    assert activate_resp.status_code == 200

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_activate")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


@pytest.mark.asyncio
async def test_strategy_overlay_writes_operator_audit(client, container):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Overlay Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    overlay_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}/overlay",
        json={"extra_tools": ["tick_jump_guard", "liq_vacuum_guard"]},
    )
    assert overlay_resp.status_code == 200
    assert overlay_resp.json()["extra_tools"] == ["tick_jump_guard", "liq_vacuum_guard"]

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_overlay_update")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


# ── Bots ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_models_update_writes_operator_audit(client, container):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Models Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    models_resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}/models",
        json={
            "signal": "gpt-5.4-mini",
            "validator": "gpt-5.4",
            "signal_instruction": "Prefer momentum continuation only.",
            "validator_instruction": "Reject weak continuation setups.",
        },
    )
    assert models_resp.status_code == 200
    assert models_resp.json()["ai_models"]["signal"] == "gpt-5.4-mini"
    assert models_resp.json()["ai_models"]["validator"] == "gpt-5.4"
    assert models_resp.json()["signal_mode"] == "ai_signal"
    assert models_resp.json()["signal_instruction"] == "Prefer momentum continuation only."
    assert models_resp.json()["validator_instruction"] == "Reject weak continuation setups."

    strategy_path = (
        strategies_route.STRATEGY_VERSIONS_DIR
        / f"{created['symbol']}_v{created['version']}.json"
    )
    saved = json.loads(strategy_path.read_text())
    assert saved["strategy_spec"]["signal_mode"] == "ai_signal"
    assert (
        saved["strategy_spec"]["prompt_bundle"]["signal_instruction"]
        == "Prefer momentum continuation only."
    )
    assert (
        saved["strategy_spec"]["prompt_bundle"]["validator_instruction"]
        == "Reject weak continuation setups."
    )

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_models_update")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


@pytest.mark.asyncio
async def test_strategy_models_update_uses_effective_spec_source_for_mode_validation(client, monkeypatch, tmp_path):
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", tmp_path)
    (tmp_path / "XAUUSD_v9.json").write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 9,
        "status": "candidate",
        "source": "",
        "signal_mode": "ai_signal",
        "signal_instruction": "legacy signal",
        "validator_instruction": "legacy validator",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }, indent=2))

    resp = await client.put(
        "/api/strategies/XAUUSD/v9/models",
        json={"signal_mode": "algo_only"},
    )

    assert resp.status_code == 400
    assert "AI Signal Discovery cards must stay in ai_signal mode" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_strategy_models_update_preserves_spec_first_signal_mode_on_prompt_only_edit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", tmp_path)
    version_file = tmp_path / "XAUUSD_v10.json"
    version_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 10,
        "status": "candidate",
        "source": "ui_ai_signal_card",
        "signal_mode": "algo_only",
        "signal_instruction": "legacy signal",
        "validator_instruction": "legacy validator",
        "ai_models": {"signal": "gemini-2.5-flash-lite"},
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }, indent=2))

    resp = await client.put(
        "/api/strategies/XAUUSD/v10/models",
        json={"validator_instruction": "updated validator"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["signal_mode"] == "ai_signal"
    assert body["validator_instruction"] == "updated validator"

    saved = json.loads(version_file.read_text())
    assert saved["signal_mode"] == "ai_signal"
    assert saved["strategy_spec"]["signal_mode"] == "ai_signal"
    assert saved["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "spec signal"
    assert saved["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "updated validator"


@pytest.mark.asyncio
async def test_strategy_update_preserves_spec_prompts_on_non_prompt_edit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", tmp_path)
    version_file = tmp_path / "XAUUSD_v11.json"
    version_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 11,
        "status": "candidate",
        "source": "ui_ai_signal_card",
        "signal_mode": "ai_signal",
        "signal_instruction": "",
        "validator_instruction": "",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }, indent=2))

    resp = await client.put(
        "/api/strategies/XAUUSD/v11",
        json={"name": "Renamed Only"},
    )

    assert resp.status_code == 200
    saved = json.loads(version_file.read_text())
    assert saved["name"] == "Renamed Only"
    assert saved["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "spec signal"
    assert saved["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "spec validator"


@pytest.mark.asyncio
async def test_strategy_promote_writes_operator_audit(client, container, monkeypatch):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Promote Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    import alphaloop.backtester.deployment_pipeline as deployment_pipeline_module

    monkeypatch.setattr(
        deployment_pipeline_module.DeploymentPipeline,
        "promote",
        AsyncMock(return_value={
            "promoted": True,
            "new_status": "dry_run",
            "reason": "promotion approved in test",
        }),
    )

    promote_resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/promote",
        json={"cycles_completed": 5},
    )
    assert promote_resp.status_code == 200
    assert promote_resp.json()["promoted"] is True
    assert promote_resp.json()["new_status"] == "dry_run"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_promote")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


@pytest.mark.asyncio
async def test_strategy_canary_start_writes_operator_audit(client, container, monkeypatch):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Canary Start Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    import alphaloop.backtester.deployment_pipeline as deployment_pipeline_module

    monkeypatch.setattr(
        deployment_pipeline_module.DeploymentPipeline,
        "start_canary",
        AsyncMock(return_value={
            "status": "ok",
            "canary_id": f"canary_{created['symbol']}_{created['version']}",
        }),
    )

    canary_resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/canary/start",
        json={"allocation_pct": 12.5, "duration_hours": 8},
    )
    assert canary_resp.status_code == 200
    assert canary_resp.json()["status"] == "ok"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_canary_start")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


@pytest.mark.asyncio
async def test_strategy_canary_end_writes_operator_audit(client, container, monkeypatch):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Canary End Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    import alphaloop.backtester.deployment_pipeline as deployment_pipeline_module

    monkeypatch.setattr(
        deployment_pipeline_module.DeploymentPipeline,
        "end_canary",
        AsyncMock(return_value={
            "status": "ok",
            "recommendation": "promote",
        }),
    )

    canary_resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/canary/end",
    )
    assert canary_resp.status_code == 200
    assert canary_resp.json()["recommendation"] == "promote"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_canary_end")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


@pytest.mark.asyncio
async def test_strategy_models_update_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Models Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}/models",
        json={"signal": "gpt-5.4-mini"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_strategy_promote_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Promote Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/promote",
        json={"cycles_completed": 2},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_strategy_canary_start_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Canary Start Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/canary/start",
        json={"allocation_pct": 10.0, "duration_hours": 4},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_strategy_canary_end_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Canary End Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/canary/end",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_strategy_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Delete Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.delete(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_strategy_writes_operator_audit(client, container):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Delete Audit Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    delete_resp = await client.delete(
        f"/api/strategies/{created['symbol']}/v{created['version']}",
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] == f"{created['symbol']}_v{created['version']}"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "strategy_delete")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == f"{created['symbol']}_v{created['version']}" for row in audit_rows)


@pytest.mark.asyncio
async def test_activate_strategy_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Activation Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.post(
        f"/api/strategies/{created['symbol']}/v{created['version']}/activate",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_strategy_overlay_requires_operator_auth(client):
    create_resp = await client.post(
        "/api/strategies/ai-signal",
        json={
            "symbol": "XAUUSD",
            "name": "Overlay Auth Card",
            "source": "ui_ai_signal_card",
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["strategy"]

    resp = await client.put(
        f"/api/strategies/{created['symbol']}/v{created['version']}/overlay",
        json={"extra_tools": ["tick_jump_guard"]},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_bots(client):
    resp = await client.get("/api/bots")
    assert resp.status_code == 200
    data = resp.json()
    assert "bots" in data
    assert isinstance(data["bots"], list)


@pytest.mark.asyncio
async def test_register_bot_requires_operator_auth(client):
    resp = await client.post(
        "/api/bots",
        json={
            "symbol": "XAUUSD",
            "instance_id": "legacy-register-auth",
            "pid": 1234,
            "strategy_version": "v1",
        },
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unregister_bot_requires_operator_auth(client, container):
    async with container.db_session_factory() as session:
        session.add(
            RunningInstance(
                symbol="XAUUSD",
                instance_id="legacy-unregister-auth",
                pid=99991,
                strategy_version="v1",
            )
        )
        await session.commit()

    resp = await client.delete(
        "/api/bots/legacy-unregister-auth",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_and_unregister_bot_write_operator_audit(client, container):
    register_resp = await client.post(
        "/api/bots",
        json={
            "symbol": "XAUUSD",
            "instance_id": "legacy-bot-1",
            "pid": 4567,
            "strategy_version": "v1",
        },
    )
    assert register_resp.status_code == 200
    assert register_resp.json()["bot"]["instance_id"] == "legacy-bot-1"

    unregister_resp = await client.delete("/api/bots/legacy-bot-1")
    assert unregister_resp.status_code == 200
    assert unregister_resp.json()["removed"] == "legacy-bot-1"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(
                OperatorAuditLog.action.in_(["bot_register", "bot_unregister"])
            )
        )).scalars())
    actions = {row.action for row in audit_rows if row.target == "legacy-bot-1"}
    assert "bot_register" in actions
    assert "bot_unregister" in actions


@pytest.mark.asyncio
async def test_start_bot_requires_operator_auth(client):
    resp = await client.post(
        "/api/bots/start",
        json={"symbol": "XAUUSD", "dry_run": True},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stop_bot_requires_operator_auth(client, container):
    async with container.db_session_factory() as session:
        session.add(
            RunningInstance(
                symbol="XAUUSD",
                instance_id="test-stop-bot",
                pid=99999,
                strategy_version="v1",
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/bots/test-stop-bot/stop",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_start_bot_writes_operator_audit(client, container, monkeypatch):
    class _FakeProc:
        pid = 4242

    monkeypatch.setattr(bots_route.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())

    resp = await client.post(
        "/api/bots/start",
        json={"symbol": "XAUUSD", "dry_run": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    instance_id = data["instance_id"]

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "bot_start")
        )).scalars())

    assert len(audit_rows) == 1
    assert audit_rows[0].target == instance_id
    assert "XAUUSD:4242:dry_run" == audit_rows[0].new_value


@pytest.mark.asyncio
async def test_start_bot_binds_strategy_from_shared_versions_dir(client, container, monkeypatch, tmp_path):
    strategy_file = tmp_path / "XAUUSD_v7.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 7,
        "status": "dry_run",
        "signal_mode": "algo_only",
        "summary": {"sharpe_ratio": 1.2},
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
        },
    }))

    class _FakeProc:
        pid = 5252

    monkeypatch.setattr(bots_route, "STRATEGY_VERSIONS_DIR", tmp_path)
    monkeypatch.setattr(bots_route.subprocess, "Popen", lambda *args, **kwargs: _FakeProc())

    resp = await client.post(
        "/api/bots/start",
        json={"symbol": "XAUUSD", "dry_run": True, "strategy_version": 7},
    )
    assert resp.status_code == 200
    instance_id = resp.json()["instance_id"]

    from alphaloop.config.settings_service import SettingsService

    settings_svc = SettingsService(container.db_session_factory)
    raw = await settings_svc.get(f"active_strategy_{instance_id}")
    assert raw is not None
    payload = json.loads(raw)
    assert payload["version"] == 7
    assert payload["signal_mode"] == "ai_signal"
    assert payload["signal_instruction"] == "spec signal"
    assert payload["summary"]["sharpe"] == 1.2

    async with container.db_session_factory() as session:
        result = await session.execute(
            select(RunningInstance).where(RunningInstance.instance_id == instance_id)
        )
        row = result.scalar_one_or_none()
        assert row is not None
        assert row.instance_id == instance_id
        assert row.strategy_version == "v7"


@pytest.mark.asyncio
async def test_stop_bot_writes_operator_audit(client, container, monkeypatch):
    async with container.db_session_factory() as session:
        session.add(
            RunningInstance(
                symbol="XAUUSD",
                instance_id="test-stop-success",
                pid=31337,
                strategy_version="v1",
            )
        )
        await session.commit()

    class _RunResult:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(bots_route.subprocess, "run", lambda *args, **kwargs: _RunResult())
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    resp = await client.post("/api/bots/test-stop-success/stop")
    assert resp.status_code == 200
    assert resp.json()["stop_method"] == "graceful (sentinel)"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "bot_stop")
        )).scalars())

    assert len(audit_rows) == 1
    assert audit_rows[0].target == "test-stop-success"
    assert audit_rows[0].old_value == "XAUUSD:31337:running"
    assert audit_rows[0].new_value == "graceful (sentinel)"


# ── Tools (Pipeline) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tools(client):
    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "decisions" in data
    assert isinstance(data["decisions"], list)


@pytest.mark.asyncio
async def test_tools_returns_durable_candidate_journey(client, container):
    async with container.db_session_factory() as session:
        session.add(
            PipelineDecision(
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
                            {"stage": "market_gate", "status": "passed", "detail": "tradeable"},
                            {"stage": "risk_gate", "status": "blocked", "detail": "portfolio heat cap"},
                        ],
                    },
                    "construction_source": "swing_low",
                },
                instance_id="test-1",
            )
        )
        await session.commit()

    resp = await client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    decision = next(item for item in data["decisions"] if item["blocked_by"] == "risk_gate")
    assert decision["construction_source"] == "swing_low"
    assert decision["journey"]["final_outcome"] == "rejected"
    assert decision["journey"]["stages"][-1]["stage"] == "risk_gate"


# ── AI Hub ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_hub(client):
    resp = await client.get("/api/ai-hub")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "providers" in data
    assert isinstance(data["providers"], list)


# ── Research ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_research(client):
    resp = await client.get("/api/research")
    assert resp.status_code == 200
    data = resp.json()
    assert "reports" in data
    assert isinstance(data["reports"], list)


@pytest.mark.asyncio
async def test_research_route_normalizes_canonical_metric_aliases(client, container):
    now = datetime.now(timezone.utc)
    async with container.db_session_factory() as session:
        session.add(ResearchReport(
            symbol="XAUUSD",
            strategy_version="v12",
            report_date=now,
            total_trades=21,
            win_rate=0.57,
            avg_rr=1.4,
            total_pnl_usd=321.5,
            sharpe_ratio=1.18,
            max_drawdown_pct=-6.2,
            analysis_summary="steady improvement",
        ))
        await session.commit()

    resp = await client.get("/api/research?symbol=XAUUSD")
    assert resp.status_code == 200
    report = resp.json()["reports"][0]
    assert report["total_pnl_usd"] == 321.5
    assert report["sharpe_ratio"] == 1.18
    assert report["max_drawdown_pct"] == -6.2
    assert report["total_pnl"] == 321.5
    assert report["sharpe"] == 1.18
    assert report["max_dd_pct"] == -6.2


@pytest.mark.asyncio
async def test_acknowledge_alert_requires_operator_auth(client, container):
    class _AlertEngine:
        rules_summary = []

        def get_all_alerts(self, limit=50):
            return [{"id": 0, "message": "test"}]

        def get_active_alerts(self):
            return [{"id": 0, "message": "test"}]

        def acknowledge(self, index: int) -> bool:
            return index == 0

    container.alert_engine = _AlertEngine()

    resp = await client.post(
        "/api/alerts/acknowledge/0",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_acknowledge_alert_writes_operator_audit(client, container):
    class _AlertEngine:
        rules_summary = []

        def get_all_alerts(self, limit=50):
            return [{"id": 0, "message": "test"}]

        def get_active_alerts(self):
            return [{"id": 0, "message": "test"}]

        def acknowledge(self, index: int) -> bool:
            return index == 0

    container.alert_engine = _AlertEngine()

    resp = await client.post("/api/alerts/acknowledge/0")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "alert_acknowledge")
        )).scalars())
    assert len(audit_rows) >= 1
    assert audit_rows[-1].target == "0"


@pytest.mark.asyncio
async def test_clear_events_requires_operator_auth(client):
    resp = await client.delete(
        "/api/events",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_clear_events_writes_operator_audit(client, container):
    event_log_route.record_event(SimpleNamespace(symbol="XAUUSD", instance_id="test-instance"))

    before_resp = await client.get("/api/events")
    assert before_resp.status_code == 200
    assert before_resp.json()["total"] >= 1

    clear_resp = await client.delete("/api/events")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["ok"] is True

    after_resp = await client.get("/api/events")
    assert after_resp.status_code == 200
    assert after_resp.json()["total"] == 0

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "event_log_clear")
        )).scalars())
    assert len(audit_rows) >= 1
    assert audit_rows[-1].target == "event_buffers"


@pytest.mark.asyncio
async def test_ai_hub_update_requires_operator_auth(client):
    resp = await client.put(
        "/api/ai-hub",
        json={"settings": {"default_signal_model": "gpt-5.4-mini"}},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_ai_hub_update_writes_operator_audit(client, container):
    resp = await client.put(
        "/api/ai-hub",
        json={"settings": {"default_signal_model": "gpt-5.4-mini"}},
    )
    assert resp.status_code == 200

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "ai_hub_update")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == "default_signal_model" for row in audit_rows)


@pytest.mark.asyncio
async def test_assets(client):
    resp = await client.get("/api/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert "assets" in data
    assert isinstance(data["assets"], list)


@pytest.mark.asyncio
async def test_asset_tools_update_requires_operator_auth(client):
    resp = await client.put(
        "/api/assets/XAUUSD/tools",
        json={"tools": {"session": True, "vwap": True}},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_asset_tools_update_writes_operator_audit(client, container):
    resp = await client.put(
        "/api/assets/XAUUSD/tools",
        json={"tools": {"session": True, "vwap": True, "unknown": True}},
    )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "XAUUSD"
    assert "unknown" not in resp.json()["tools"]

    async with container.db_session_factory() as session:
        audit_rows = list((await session.execute(
            select(OperatorAuditLog).where(OperatorAuditLog.action == "asset_tools_update")
        )).scalars())
    assert len(audit_rows) >= 1
    assert any(row.target == "XAUUSD" for row in audit_rows)
