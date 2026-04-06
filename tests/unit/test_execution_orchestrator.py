"""Unit tests for trading.execution_orchestrator.ExecutionOrchestrator."""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from alphaloop.core.events import EventBus, TradeOpened
from alphaloop.trading.execution_orchestrator import ExecutionOrchestrator, ExecutionOutcome


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_signal(
    direction="BUY",
    setup_type="pullback",
    entry_zone=(2350.0, 2355.0),
    stop_loss=2340.0,
    take_profit=None,
    raw_confidence=0.80,
    reasoning="test signal v4 pipeline generated hypothesis",
    rr_ratio=2.0,
):
    s = MagicMock()
    s.direction = direction
    s.setup_type = setup_type
    s.entry_zone = entry_zone
    s.stop_loss = stop_loss
    s.take_profit = take_profit or [2380.0]
    s.raw_confidence = raw_confidence
    s.reasoning = reasoning
    s.rr_ratio = rr_ratio
    return s


def _make_sizing(
    conviction_scalar=1.0,
    regime_scalar=1.0,
    freshness_scalar=1.0,
    risk_gate_scalar=1.0,
    equity_curve_scalar=1.0,
):
    s = MagicMock()
    s.conviction_scalar = conviction_scalar
    s.regime_scalar = regime_scalar
    s.freshness_scalar = freshness_scalar
    s.risk_gate_scalar = risk_gate_scalar
    s.equity_curve_scalar = equity_curve_scalar
    return s


def _make_result(signal=None, sizing=None):
    r = MagicMock()
    r.signal = signal or _make_signal()
    r.sizing = sizing or _make_sizing()
    return r


def _make_sizer(lots=0.10, risk_amount_usd=50.0):
    sizer = MagicMock()
    sizer.compute_lot_size = MagicMock(
        return_value={"lots": lots, "risk_amount_usd": risk_amount_usd}
    )
    sizer.account_balance = 10000.0
    return sizer


def _make_exec_report(status="FILLED", broker_ticket=12345, fill_price=2352.5):
    from alphaloop.execution.service import ExecutionReport
    return ExecutionReport(
        status=status,
        broker_ticket=broker_ticket,
        fill_price=fill_price,
    )


def _make_execution_service(report=None):
    svc = MagicMock()
    svc.execute_market_order = AsyncMock(return_value=report or _make_exec_report())
    return svc


def _make_orchestrator(
    *,
    sizer=None,
    execution_service=None,
    event_bus=None,
    risk_monitor=None,
    notifier=None,
    settings_service=None,
    guard_state_refs=None,
    dry_run=True,
):
    if event_bus is None:
        event_bus = EventBus()
    orch = ExecutionOrchestrator(
        sizer=sizer or _make_sizer(),
        execution_service=execution_service or _make_execution_service(),
        event_bus=event_bus,
        symbol="XAUUSD",
        instance_id="test-1",
        dry_run=dry_run,
        risk_monitor=risk_monitor,
        notifier=notifier,
        settings_service=settings_service,
        guard_state_refs=guard_state_refs,
    )
    # Set a minimal active_strategy
    strategy = MagicMock()
    strategy.version = 5
    orch.update_state(active_strategy=strategy, canary_allocation=None)
    return orch


# ---------------------------------------------------------------------------
# ExecutionOutcome dataclass
# ---------------------------------------------------------------------------

def test_execution_outcome_defaults():
    o = ExecutionOutcome(status="FILLED")
    assert o.broker_ticket is None
    assert o.fill_price is None
    assert o.lots == 0.0
    assert o.error_message == ""


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------

def test_safe_json_payload_dict():
    result = ExecutionOrchestrator._safe_json_payload({"a": 1})
    assert result == {"a": 1}


def test_safe_json_payload_none():
    assert ExecutionOrchestrator._safe_json_payload(None) is None


def test_safe_json_payload_plain_value():
    result = ExecutionOrchestrator._safe_json_payload("hello")
    assert result == {"value": "hello"}


def test_session_name_from_context_dict():
    ctx = {"session": {"name": "london_session"}}
    assert ExecutionOrchestrator._session_name_from_context(ctx) == "london_session"


def test_session_name_from_context_object():
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.name = "ny_session"
    assert ExecutionOrchestrator._session_name_from_context(ctx) == "ny_session"


def test_session_name_from_context_empty():
    assert ExecutionOrchestrator._session_name_from_context({}) == ""


# ---------------------------------------------------------------------------
# update_state
# ---------------------------------------------------------------------------

def test_update_state_sets_active_strategy():
    orch = _make_orchestrator()
    new_strat = MagicMock()
    new_strat.version = 9
    orch.update_state(active_strategy=new_strat, canary_allocation=0.5)
    assert orch._active_strategy is new_strat
    assert orch._canary_allocation == 0.5


def test_update_state_sizer_kwarg():
    orch = _make_orchestrator()
    new_sizer = MagicMock()
    orch.update_state(active_strategy=orch._active_strategy, canary_allocation=None, sizer=new_sizer)
    assert orch._sizer is new_sizer


# ---------------------------------------------------------------------------
# execute — FILLED path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_filled_returns_filled_outcome():
    orch = _make_orchestrator(
        execution_service=_make_execution_service(_make_exec_report(status="FILLED", broker_ticket=99, fill_price=2353.0)),
    )
    result = _make_result()
    outcome = await orch.execute(result, {})
    assert outcome.status == "FILLED"
    assert outcome.broker_ticket == 99
    assert outcome.fill_price == 2353.0


@pytest.mark.asyncio
async def test_execute_filled_calls_risk_monitor_register_open():
    risk_monitor = MagicMock()
    risk_monitor.register_open = AsyncMock()

    orch = _make_orchestrator(
        execution_service=_make_execution_service(_make_exec_report(status="FILLED")),
        risk_monitor=risk_monitor,
    )
    await orch.execute(_make_result(), {})
    risk_monitor.register_open.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_filled_publishes_trade_opened_event():
    received = []

    bus = EventBus()
    bus.subscribe(TradeOpened, lambda e: received.append(e))

    orch = _make_orchestrator(
        event_bus=bus,
        execution_service=_make_execution_service(_make_exec_report(status="FILLED", fill_price=2360.0)),
    )
    await orch.execute(_make_result(), {})

    assert len(received) == 1
    evt = received[0]
    assert evt.symbol == "XAUUSD"
    assert evt.entry_price == 2360.0


@pytest.mark.asyncio
async def test_execute_filled_calls_notifier():
    notifier = MagicMock()
    notifier.alert_trade_opened = AsyncMock()

    orch = _make_orchestrator(
        execution_service=_make_execution_service(_make_exec_report(status="FILLED")),
        notifier=notifier,
    )
    await orch.execute(_make_result(), {})
    notifier.alert_trade_opened.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_filled_saves_guard_state():
    settings_service = MagicMock()
    guard_refs = {
        "hash_filter": MagicMock(),
        "conf_variance": MagicMock(),
        "spread_regime": MagicMock(),
        "equity_scaler": MagicMock(),
        "dd_pause": MagicMock(),
    }

    with patch("alphaloop.trading.execution_orchestrator.save_guard_state", new_callable=AsyncMock) as mock_save:
        orch = _make_orchestrator(
            execution_service=_make_execution_service(_make_exec_report(status="FILLED")),
            settings_service=settings_service,
            guard_state_refs=guard_refs,
        )
        await orch.execute(_make_result(), {})
        mock_save.assert_awaited_once()


# ---------------------------------------------------------------------------
# execute — non-FILLED paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_blocked_does_not_register_open():
    risk_monitor = MagicMock()
    risk_monitor.register_open = AsyncMock()

    orch = _make_orchestrator(
        execution_service=_make_execution_service(_make_exec_report(status="BLOCKED", broker_ticket=None, fill_price=None)),
        risk_monitor=risk_monitor,
    )
    outcome = await orch.execute(_make_result(), {})
    assert outcome.status == "BLOCKED"
    risk_monitor.register_open.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_failed_does_not_call_notifier():
    notifier = MagicMock()
    notifier.alert_trade_opened = AsyncMock()

    orch = _make_orchestrator(
        execution_service=_make_execution_service(_make_exec_report(status="FAILED", broker_ticket=None, fill_price=None)),
        notifier=notifier,
    )
    outcome = await orch.execute(_make_result(), {})
    assert outcome.status == "FAILED"
    notifier.alert_trade_opened.assert_not_awaited()


# ---------------------------------------------------------------------------
# execute — sizer edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_no_sizer_returns_failed():
    orch = _make_orchestrator(sizer=None)
    orch._sizer = None  # ensure None
    outcome = await orch.execute(_make_result(), {})
    assert outcome.status == "FAILED"
    assert "Sizer" in outcome.error_message


@pytest.mark.asyncio
async def test_execute_sizer_raises_returns_failed():
    sizer = MagicMock()
    sizer.compute_lot_size = MagicMock(side_effect=ValueError("Invalid ATR"))

    orch = _make_orchestrator(sizer=sizer)
    outcome = await orch.execute(_make_result(), {})
    assert outcome.status == "FAILED"
    assert "Sizer" in outcome.error_message


# ---------------------------------------------------------------------------
# execute — canary allocation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_canary_allocation_reduces_lots():
    captured_sizing = []

    async def _mock_execute(**kwargs):
        captured_sizing.append(kwargs["sizing"]["lots"])
        return _make_exec_report(status="FILLED")

    exec_svc = MagicMock()
    exec_svc.execute_market_order = _mock_execute

    sizer = _make_sizer(lots=0.20)
    orch = _make_orchestrator(sizer=sizer, execution_service=exec_svc)
    orch.update_state(
        active_strategy=orch._active_strategy,
        canary_allocation=0.5,
    )

    await orch.execute(_make_result(), {})

    submitted_lots = captured_sizing[0]
    assert submitted_lots <= 0.11  # 0.20 * 1.0 combined * 0.5 canary = 0.10


@pytest.mark.asyncio
async def test_execute_no_canary_full_lots():
    captured_sizing = []

    async def _mock_execute(**kwargs):
        captured_sizing.append(kwargs["sizing"]["lots"])
        return _make_exec_report(status="FILLED")

    exec_svc = MagicMock()
    exec_svc.execute_market_order = _mock_execute

    sizer = _make_sizer(lots=0.10)
    orch = _make_orchestrator(sizer=sizer, execution_service=exec_svc)
    orch.update_state(
        active_strategy=orch._active_strategy,
        canary_allocation=None,  # no canary
    )

    await orch.execute(_make_result(), {})
    assert captured_sizing[0] == pytest.approx(0.10, abs=0.001)


# ---------------------------------------------------------------------------
# execute — combined scalar
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_combined_scalar_reduces_lots():
    captured_sizing = []

    async def _mock_execute(**kwargs):
        captured_sizing.append(kwargs["sizing"]["lots"])
        return _make_exec_report(status="FILLED")

    exec_svc = MagicMock()
    exec_svc.execute_market_order = _mock_execute

    sizing = _make_sizing(
        conviction_scalar=0.5,
        regime_scalar=0.8,
        freshness_scalar=1.0,
        risk_gate_scalar=1.0,
        equity_curve_scalar=1.0,
    )  # combined = 0.4

    sizer = _make_sizer(lots=0.20)
    orch = _make_orchestrator(sizer=sizer, execution_service=exec_svc)

    await orch.execute(_make_result(sizing=sizing), {})
    # 0.20 * 0.4 = 0.08 → round to 0.08
    assert captured_sizing[0] == pytest.approx(0.08, abs=0.01)


# ---------------------------------------------------------------------------
# execute — notifier error is non-fatal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_notifier_exception_does_not_abort():
    notifier = MagicMock()
    notifier.alert_trade_opened = AsyncMock(side_effect=RuntimeError("Telegram down"))

    orch = _make_orchestrator(
        execution_service=_make_execution_service(_make_exec_report(status="FILLED")),
        notifier=notifier,
    )
    # Should not raise
    outcome = await orch.execute(_make_result(), {})
    assert outcome.status == "FILLED"


# ---------------------------------------------------------------------------
# active_strategy_id helper
# ---------------------------------------------------------------------------

def test_active_strategy_id_with_strategy():
    orch = _make_orchestrator()
    orch._active_strategy.version = 7
    assert orch._active_strategy_id() == "XAUUSD.v7"


def test_active_strategy_id_no_strategy():
    orch = _make_orchestrator()
    orch._active_strategy = None
    assert orch._active_strategy_id() == "XAUUSD"


def test_active_strategy_id_prefers_spec_first_runtime_context():
    orch = _make_orchestrator()
    orch._active_strategy = SimpleNamespace(
        version="legacy",
        strategy_spec=SimpleNamespace(spec_version="v1"),
    )
    assert orch._active_strategy_id() == "XAUUSD"


@pytest.mark.asyncio
async def test_submit_prefers_spec_first_runtime_version_for_execution_metadata():
    captured = {}

    async def _mock_execute(**kwargs):
        captured.update(kwargs)
        return _make_exec_report(status="FILLED")

    exec_svc = MagicMock()
    exec_svc.execute_market_order = _mock_execute

    orch = _make_orchestrator(execution_service=exec_svc)
    orch._active_strategy = SimpleNamespace(
        version="legacy",
        signal_mode="algo_only",
        strategy_spec=SimpleNamespace(
            spec_version="v1",
            signal_mode="ai_signal",
            metadata={"version": 9},
        ),
    )

    await orch.execute(_make_result(), {})

    assert captured["strategy_id"] == "XAUUSD"
    assert captured["strategy_version"] == ""
