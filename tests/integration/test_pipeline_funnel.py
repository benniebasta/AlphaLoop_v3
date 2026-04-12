"""Integration test for the Gate-1 pipeline observability endpoints.

Seeds ``pipeline_stage_decisions`` with synthetic rows and asserts that
``/api/pipeline/funnel``, ``/api/pipeline/stages/heatmap``,
``/api/pipeline/modes/compare`` and ``/api/pipeline/decisions/latest`` all
return the expected aggregates.

No trading loop is started — these tests exercise the ledger → endpoint path
in isolation.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from alphaloop.db.models.pipeline import (
    PipelineDecision,
    PipelineStageDecision,
)
from alphaloop.webui.app import create_webui_app


_AUTH = "test-auth-funnel"


@pytest_asyncio.fixture
async def funnel_client(container, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", _AUTH)
    app = create_webui_app(container)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_AUTH}"},
    ) as c:
        yield c


async def _seed(session_factory, rows: list[dict]) -> None:
    async with session_factory() as session:
        for r in rows:
            session.add(PipelineStageDecision(**r))
        await session.commit()


def _row(
    *,
    cycle_id: str,
    stage: str,
    status: str,
    blocked_by: str | None = None,
    outcome: str | None = None,
    symbol: str = "XAUUSD",
    mode: str = "algo_only",
    source: str = "live",
    occurred_at: datetime | None = None,
    stage_index: int = 0,
) -> dict:
    return {
        "occurred_at": occurred_at or datetime.now(timezone.utc),
        "cycle_id": cycle_id,
        "source": source,
        "symbol": symbol,
        "instance_id": "test-instance",
        "mode": mode,
        "stage": stage,
        "stage_index": stage_index,
        "status": status,
        "blocked_by": blocked_by,
        "detail": None,
        "payload": None,
        "outcome": outcome,
        "reject_stage": blocked_by,
        "direction": "BUY",
        "setup_type": "pullback",
        "conviction_score": None,
        "size_multiplier": 1.0,
        "latency_ms": 10.0,
    }


@pytest.mark.asyncio
async def test_funnel_endpoint_aggregates_by_stage(container, funnel_client):
    factory = container.db_session_factory

    # Three synthetic cycles:
    #  cycle-A: passes market_gate, held at conviction
    #  cycle-B: passes market_gate, rejected at invalidation
    #  cycle-C: fully executed
    rows = [
        # cycle-A
        _row(cycle_id="cycle-A", stage="market_gate", status="passed", outcome="held", stage_index=0),
        _row(cycle_id="cycle-A", stage="signal", status="signal_generated", outcome="held", stage_index=1),
        _row(cycle_id="cycle-A", stage="conviction", status="held",
             blocked_by="conviction", outcome="held", stage_index=2),
        # cycle-B
        _row(cycle_id="cycle-B", stage="market_gate", status="passed", outcome="rejected", stage_index=0),
        _row(cycle_id="cycle-B", stage="invalidation", status="hard_invalidated",
             blocked_by="invalidation", outcome="rejected", stage_index=1),
        # cycle-C
        _row(cycle_id="cycle-C", stage="market_gate", status="passed", outcome="trade_opened", stage_index=0),
        _row(cycle_id="cycle-C", stage="signal", status="signal_generated", outcome="trade_opened", stage_index=1),
        _row(cycle_id="cycle-C", stage="conviction", status="trade", outcome="trade_opened", stage_index=2),
        _row(cycle_id="cycle-C", stage="execution_guard", status="execute", outcome="trade_opened", stage_index=3),
    ]
    await _seed(factory, rows)

    resp = await funnel_client.get("/api/pipeline/funnel?hours=24&source=live")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_cycles"] == 3
    assert data["executed_cycles"] == 1
    stages = {s["stage"]: s for s in data["stages"]}

    # market_gate saw 3 cycles, all passed
    assert stages["market_gate"]["total"] == 3
    assert stages["market_gate"]["passed"] == 3
    assert stages["market_gate"]["blocked"] == 0

    # conviction saw 2 rows: one held, one trade
    assert stages["conviction"]["held"] == 1
    assert stages["conviction"]["passed"] == 1
    conviction_reasons = {r["reason"] for r in stages["conviction"]["top_reasons"]}
    assert "conviction" in conviction_reasons

    # invalidation saw 1 row: hard-blocked
    assert stages["invalidation"]["total"] == 1
    assert stages["invalidation"]["blocked"] == 1


@pytest.mark.asyncio
async def test_funnel_filters_by_symbol_and_mode(container, funnel_client):
    factory = container.db_session_factory
    rows = [
        _row(cycle_id="eu-1", stage="market_gate", status="passed", symbol="EURUSD",
             mode="algo_ai", outcome="trade_opened"),
        _row(cycle_id="xau-1", stage="market_gate", status="blocked", blocked_by="market_gate",
             symbol="XAUUSD", mode="algo_only", outcome="rejected"),
    ]
    await _seed(factory, rows)

    resp = await funnel_client.get("/api/pipeline/funnel?symbol=EURUSD&mode=algo_ai&hours=24")
    data = resp.json()
    assert data["total_cycles"] == 1
    assert data["executed_cycles"] == 1
    stages = {s["stage"]: s for s in data["stages"]}
    assert stages["market_gate"]["passed"] == 1
    assert stages["market_gate"]["blocked"] == 0


@pytest.mark.asyncio
async def test_heatmap_returns_stage_symbol_matrix(container, funnel_client):
    factory = container.db_session_factory
    rows = [
        _row(cycle_id="c1", stage="conviction", status="held", blocked_by="conviction",
             symbol="XAUUSD", outcome="held"),
        _row(cycle_id="c2", stage="conviction", status="trade",
             symbol="XAUUSD", outcome="trade_opened"),
        _row(cycle_id="c3", stage="conviction", status="held", blocked_by="conviction",
             symbol="EURUSD", outcome="held"),
    ]
    await _seed(factory, rows)

    resp = await funnel_client.get("/api/pipeline/stages/heatmap?hours=24")
    assert resp.status_code == 200
    data = resp.json()
    assert "conviction" in data["stages"]
    assert "XAUUSD" in data["symbols"]
    cells_by_key = {(c["stage"], c["symbol"]): c for c in data["cells"]}
    xau = cells_by_key[("conviction", "XAUUSD")]
    assert xau["total"] == 2
    assert xau["held"] == 1
    assert 0.49 <= xau["rejection_rate"] <= 0.51


@pytest.mark.asyncio
async def test_mode_compare_groups_by_mode(container, funnel_client):
    factory = container.db_session_factory
    rows = [
        _row(cycle_id="m-a1", stage="market_gate", status="passed", mode="algo_only",
             outcome="trade_opened"),
        _row(cycle_id="m-a2", stage="market_gate", status="passed", mode="algo_only",
             outcome="rejected"),
        _row(cycle_id="m-b1", stage="market_gate", status="passed", mode="ai_signal",
             outcome="trade_opened"),
    ]
    await _seed(factory, rows)

    resp = await funnel_client.get("/api/pipeline/modes/compare?hours=24")
    data = resp.json()
    by_mode = {m["mode"]: m for m in data["modes"]}
    assert by_mode["algo_only"]["total"] == 2
    assert by_mode["algo_only"]["executed"] == 1
    assert by_mode["ai_signal"]["executed"] == 1


@pytest.mark.asyncio
async def test_latest_decisions_returns_trade_decision_projection(container, funnel_client):
    factory = container.db_session_factory
    async with factory() as session:
        session.add(PipelineDecision(
            occurred_at=datetime.now(timezone.utc),
            symbol="XAUUSD",
            direction="BUY",
            allowed=True,
            blocked_by=None,
            block_reason=None,
            size_modifier=1.0,
            tool_results={
                "trade_decision": {
                    "symbol": "XAUUSD",
                    "mode": "algo_only",
                    "direction": "BUY",
                    "setup_type": "pullback",
                    "outcome": "trade_opened",
                    "reject_stage": None,
                    "reject_reason": None,
                    "confidence_raw": 0.72,
                    "confidence_adjusted": 0.74,
                    "conviction_score": 80.0,
                    "conviction_decision": "TRADE",
                    "penalties": [],
                    "size_multiplier": 0.9,
                    "hard_block": False,
                    "ai_verdict": "skipped",
                    "execution_status": "executed",
                    "latency_ms": 12.3,
                    "journey": {"stages": [], "final_outcome": "trade_opened",
                                "rejection_reason": None},
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
                "journey": None,
                "construction_source": "swing_low",
            },
            instance_id="test",
        ))
        await session.commit()

    resp = await funnel_client.get("/api/pipeline/decisions/latest?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    decision = data["decisions"][0]["decision"]
    assert decision["symbol"] == "XAUUSD"
    assert decision["outcome"] == "trade_opened"
    assert decision["conviction_score"] == 80.0
