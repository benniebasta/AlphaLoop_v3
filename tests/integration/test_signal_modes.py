"""Integration tests for the 3 signal modes: algo_only, algo_ai, ai_signal.

These tests are launched by the Test Flow WebUI panel via:
    pytest tests/integration/test_signal_modes.py -k <mode> -v

Each test:
  1. Generates a signal using the specified mode strategy
  2. Executes the signal via ExecutionService (dry run, no broker)
  3. Closes the trade via TradeRepository
  4. Asserts DB state is correct end-to-end

Pipeline filter tests (suffixed _pipeline) cover all 8 stages for each mode:
  - Happy path: signal passes every filter → TRADE_OPENED
  - Session filter block (MarketGate plugin) → REJECTED
  - Hard invalidation (bad SL direction) → REJECTED
  - Low conviction → HELD
  - Risk gate block (kill switch) → REJECTED
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from datetime import datetime, timezone

from alphaloop.core.types import TrendDirection, SetupType
from alphaloop.db.repositories.trade_repo import TradeRepository
from alphaloop.execution.schemas import OrderResult
from alphaloop.execution.service import ExecutionService
from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.orchestrator import PipelineOrchestrator
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.risk_gate import RiskGateRunner
from alphaloop.pipeline.types import CandidateSignal, CycleOutcome
from alphaloop.signals.algorithmic import AlgorithmicSignalEngine
from alphaloop.signals.schema import TradeSignal

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _algo_params() -> dict:
    """Minimal strategy params — EMA crossover, sensible R:R."""
    return {
        "signal_rules": [{"source": "ema_crossover"}],
        "signal_logic": "OR",
        "rsi_ob": 70,
        "rsi_os": 30,
        "sl_atr_mult": 1.5,
        "tp1_rr": 2.0,
        "tp2_rr": 3.0,
        "entry_zone_atr_mult": 0.25,
    }


def _bullish_context(price: float = 3102.0, atr: float = 15.0) -> dict:
    """Synthetic M15 context: fast EMA above slow EMA, RSI neutral-bullish."""
    return {
        "timeframes": {
            "M15": {
                "indicators": {
                    "ema_fast": price + 4.0,
                    "ema_slow": price - 4.0,
                    "rsi": 55.0,
                    "macd_histogram": 0.8,
                    "bb_pct_b": 0.6,
                    "adx": 28.0,
                    "plus_di": 28.0,
                    "minus_di": 18.0,
                    "atr": atr,
                }
            }
        },
        "current_price": {"bid": price, "ask": price + 1.2},
    }


def _mock_executor(fill_price: float, ticket: int) -> SimpleNamespace:
    return SimpleNamespace(
        open_order=AsyncMock(
            return_value=OrderResult(
                success=True,
                order_ticket=ticket,
                fill_price=fill_price,
                fill_volume=0.1,
            )
        )
    )


def _mock_control_plane(order_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        preflight=AsyncMock(
            return_value=SimpleNamespace(
                approved=True,
                reason="",
                order_id=order_id,
                client_order_id=f"cid-{order_id}",
                projected_risk_usd=100.0,
            )
        )
    )


async def _run_signal_flow(container, signal, *, mode_tag: str, ticket: int, order_id: str) -> None:
    """Execute signal → verify OPEN in DB → close trade → verify WIN."""
    fill_price = round(signal.entry_mid, 5)

    svc = ExecutionService(
        session_factory=container.db_session_factory,
        executor=_mock_executor(fill_price=fill_price, ticket=ticket),
        control_plane=_mock_control_plane(order_id),
        supervision_service=None,
        dry_run=True,
    )
    svc._update_order_status = AsyncMock()

    tp2 = signal.take_profit[1] if len(signal.take_profit) > 1 else None

    report = await svc.execute_market_order(
        symbol="XAUUSD",
        instance_id=f"test-{mode_tag}",
        account_balance=10_000.0,
        signal=signal,
        sizing={"lots": 0.1, "risk_pct": 1.0, "risk_amount_usd": 100.0},
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit[0],
        take_profit_2=tp2,
        strategy_version=f"{mode_tag}_test",
        is_dry_run=True,
    )

    assert report.status == "FILLED", (
        f"[{mode_tag}] Expected FILLED, got {report.status!r}. "
        f"Error: {getattr(report, 'error_message', '')}"
    )
    assert report.trade_id is not None, f"[{mode_tag}] trade_id is None"

    async with container.db_session_factory() as session:
        repo = TradeRepository(session)

        # --- Verify OPEN trade ---
        trade = await repo.get_by_id(report.trade_id)
        assert trade is not None, f"[{mode_tag}] TradeLog {report.trade_id} not found in DB"
        assert trade.outcome == "OPEN",   f"[{mode_tag}] Expected OPEN, got {trade.outcome!r}"
        assert trade.direction == signal.direction, (
            f"[{mode_tag}] direction mismatch: {trade.direction} vs {signal.direction}"
        )
        assert trade.symbol == "XAUUSD", f"[{mode_tag}] symbol mismatch"

        # --- Close the trade ---
        close_price = signal.take_profit[0]
        await repo.close_trade(
            report.trade_id,
            close_price=close_price,
            pnl_usd=50.0,
            outcome="WIN",
        )
        await session.commit()

        # --- Verify WIN ---
        closed = await repo.get_by_id(report.trade_id)
        assert closed.outcome == "WIN",       f"[{mode_tag}] Expected WIN after close"
        assert closed.pnl_usd == 50.0,        f"[{mode_tag}] pnl_usd mismatch"
        assert closed.closed_at is not None,  f"[{mode_tag}] closed_at not set"


# ---------------------------------------------------------------------------
# Test 1 — algo_only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_algo_only_signal_enters_and_closes(container):
    """algo_only mode: AlgorithmicSignalEngine (no AI) → OPEN → WIN."""
    engine = AlgorithmicSignalEngine(symbol="XAUUSD", params=_algo_params())
    # Seed prev state so EMA crossover fires this cycle (prev: fast < slow → curr: fast > slow)
    engine._prev_fast = 3097.0
    engine._prev_slow = 3099.0

    signal = await engine.generate_signal(_bullish_context())
    assert signal is not None, (
        f"[algo_only] Signal engine returned None. "
        f"Neutral reason: {engine.last_neutral_reason}"
    )
    assert signal.direction == "BUY", f"[algo_only] Expected BUY, got {signal.direction!r}"

    await _run_signal_flow(
        container, signal,
        mode_tag="algo_only", ticket=77001, order_id="ord-algo-only",
    )


# ---------------------------------------------------------------------------
# Test 2 — algo_ai
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_algo_ai_signal_enters_and_closes(container):
    """algo_ai mode: algo signal + simulated AI confidence adjustment → OPEN → WIN."""
    engine = AlgorithmicSignalEngine(symbol="XAUUSD", params=_algo_params())
    engine._prev_fast = 3097.0
    engine._prev_slow = 3099.0

    signal = await engine.generate_signal(_bullish_context())
    assert signal is not None, (
        f"[algo_ai] Signal engine returned None. "
        f"Neutral reason: {engine.last_neutral_reason}"
    )

    # Simulate AI review step: boost confidence as algo_ai mode would
    boosted_confidence = round(min(signal.confidence + 0.05, 0.90), 3)
    signal = signal.model_copy(update={"confidence": boosted_confidence})
    assert signal.direction == "BUY", f"[algo_ai] Expected BUY, got {signal.direction!r}"

    await _run_signal_flow(
        container, signal,
        mode_tag="algo_ai", ticket=77002, order_id="ord-algo-ai",
    )


# ---------------------------------------------------------------------------
# Test 3 — ai_signal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_signal_enters_and_closes(container):
    """ai_signal mode: fully constructed AI TradeSignal (no API call) → OPEN → WIN."""
    price = 3102.0
    atr = 15.0
    sl_dist = 1.5 * atr                         # 22.5
    tp1_dist = sl_dist * 2.0                    # 45.0
    tp2_dist = sl_dist * 3.0                    # 67.5
    zone_half = atr * 0.25                      # 3.75

    # Construct exactly what a well-formed AI response would produce
    signal = TradeSignal(
        trend=TrendDirection.BULLISH,
        setup=SetupType.PULLBACK,
        entry_zone=[round(price - zone_half, 5), round(price + zone_half, 5)],
        stop_loss=round(price - sl_dist, 5),    # 3079.5 — below zone
        take_profit=[
            round(price + tp1_dist, 5),         # 3147.0 — above zone
            round(price + tp2_dist, 5),         # 3169.5
        ],
        confidence=0.78,
        reasoning=(
            "AI signal: bullish structure confirmed on M15 with higher-TF EMA alignment. "
            "RSI at 55 with room to run. Entry on pullback to fast EMA."
        ),
    )
    assert signal.direction == "BUY"

    await _run_signal_flow(
        container, signal,
        mode_tag="ai_signal", ticket=77003, order_id="ord-ai-signal",
    )


# =============================================================================
# PIPELINE FILTER TESTS
# Each mode gets 5 scenarios:
#   1. Happy path — all 8 stages pass → TRADE_OPENED
#   2. Session filter block (MarketGate plugin) → REJECTED
#   3. Hard invalidation (SL on wrong side) → REJECTED
#   4. Low conviction → HELD
#   5. Risk gate block (kill switch) → REJECTED
# =============================================================================

# ---------------------------------------------------------------------------
# Shared pipeline helpers
# ---------------------------------------------------------------------------

def _make_pipeline_context(
    *,
    price: float = 3100.5,
    atr: float = 10.0,
    kill_switch: bool = False,
    can_open_trade: bool = True,
    choppiness: float = 32.0,
    adx: float = 35.0,
    tick_jump_atr: float = 0.3,
):
    """Realistic MagicMock market context for pipeline tests."""
    ctx = MagicMock()
    ctx.symbol = "XAUUSD"
    ctx.trade_direction = ""
    ctx.pip_size = 0.01
    ctx.indicators = {
        "M15": {
            "ema200": price - 12.0,
            "atr": atr,
            "choppiness": choppiness,
            "adx": adx,
            "bos": {
                "bullish_bos": True, "bullish_break_atr": 0.5,
                "bearish_bos": False, "bearish_break_atr": 0,
                "swing_high": price - 2.0, "swing_low": price - 22.0,
            },
            "fvg": {"bullish": [{"size_atr": 0.3, "bottom": price - 10, "top": price - 7, "midpoint": price - 8.5}], "bearish": []},
            "vwap": price - 5.0,
            "swing_structure": "bullish",
            "bb_pct_b": 0.45,
            "fast_fingers": {"is_exhausted_up": False, "is_exhausted_down": True, "exhaustion_score": 60},
            "tick_jump_atr": tick_jump_atr,
            "liq_vacuum": {"bar_range_atr": 1.0, "body_pct": 60},
            "median_spread": 1.5,
        },
        "H1": {"atr_pct": 0.003},
    }
    ctx.session = MagicMock(is_weekend=False, score=0.85)
    ctx.price = MagicMock(bid=price, ask=price + 0.5, spread=1.5, time=datetime.now(timezone.utc))
    ctx.news = []
    ctx.dxy = None
    ctx.sentiment = None
    ctx.open_trades = {}
    ctx.risk_monitor = SimpleNamespace(
        kill_switch_active=kill_switch,
        _kill_switch_active=kill_switch,
        _open_risk_usd=0,
        account_balance=10_000,
        can_open_trade=AsyncMock(return_value=(can_open_trade, "" if can_open_trade else "max daily loss hit")),
    )
    ctx.df = MagicMock(__len__=lambda self: 500)
    return ctx


def _make_candidate_signal(
    *,
    direction: str = "BUY",
    setup: str = "pullback",
    confidence: float = 0.75,
    stop_loss: float = 3090.0,
) -> CandidateSignal:
    return CandidateSignal(
        direction=direction,
        setup_type=setup,
        entry_zone=(3100.0, 3102.0),
        stop_loss=stop_loss,
        take_profit=[3115.0, 3125.0],
        raw_confidence=confidence,
        rr_ratio=1.5,
        signal_sources=["ema_crossover"],
        reasoning="Pipeline filter test signal",
    )


def _make_full_orchestrator() -> PipelineOrchestrator:
    """Full 8-stage orchestrator with permissive thresholds for unit testing."""
    scorer = ConvictionScorer()
    scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}
    return PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=scorer,
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )


def _session_block_plugin() -> MagicMock:
    """Mock session_filter plugin that always blocks."""
    plugin = MagicMock()
    plugin.name = "session_filter"
    plugin.timed_run = AsyncMock(return_value=SimpleNamespace(
        tool_name="session_filter",
        passed=False,
        reason="Weekend — market closed",
        severity="block",
        size_modifier=1.0,
    ))
    return plugin


# ---------------------------------------------------------------------------
# algo_only pipeline tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_algo_only_pipeline_happy_path():
    """algo_only: valid signal passes all 8 stages → TRADE_OPENED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal()

    result = await _make_full_orchestrator().run(ctx, signal_gen, symbol="XAUUSD", mode="algo_only")

    assert result.outcome == CycleOutcome.TRADE_OPENED
    assert result.market_gate.tradeable is True
    assert result.regime.regime == "trending"
    assert result.signal is not None
    assert result.conviction.decision == "TRADE"
    assert result.risk_gate.allowed is True
    assert result.execution_guard.action == "EXECUTE"


@pytest.mark.asyncio
async def test_algo_only_pipeline_session_filter_blocks():
    """algo_only: session_filter plugin in MarketGate → REJECTED at Stage 1."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal()

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(tools=[_session_block_plugin()]),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_only")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.market_gate.tradeable is False
    assert result.market_gate.blocked_by == "session_filter"
    assert result.signal is None   # pipeline stopped before signal stage


@pytest.mark.asyncio
async def test_algo_only_pipeline_hard_invalidation_rejects():
    """algo_only: SL on wrong side of entry → HARD_INVALIDATE → REJECTED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        sig = _make_candidate_signal()
        sig.stop_loss = 3115.0   # above entry on a BUY — hard invalid
        return sig

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_only")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.invalidation.severity == "HARD_INVALIDATE"


@pytest.mark.asyncio
async def test_algo_only_pipeline_low_conviction_holds():
    """algo_only: very low confidence signal → conviction HOLD → HELD."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        # confidence=0.35 passes invalidation hard min (0.30) but falls below
        # conviction min_entry threshold → HOLD
        return _make_candidate_signal(confidence=0.35)

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=ConvictionScorer(),
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_only")

    assert result.outcome == CycleOutcome.HELD
    assert result.conviction.decision == "HOLD"


@pytest.mark.asyncio
async def test_algo_only_pipeline_risk_gate_blocks():
    """algo_only: risk monitor blocks new trade (max loss hit) → RiskGate REJECTED."""
    ctx = _make_pipeline_context(can_open_trade=False)

    async def signal_gen(context, regime):
        return _make_candidate_signal()

    scorer = ConvictionScorer()
    scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}
    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=scorer,
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_only")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.risk_gate.allowed is False


# ---------------------------------------------------------------------------
# algo_ai pipeline tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_algo_ai_pipeline_happy_path():
    """algo_ai: valid signal (algo-generated, AI-boosted confidence) → TRADE_OPENED."""
    ctx = _make_pipeline_context()
    engine = AlgorithmicSignalEngine(symbol="XAUUSD", params=_algo_params())
    engine._prev_fast = 3094.0
    engine._prev_slow = 3097.0

    algo_context = {
        "timeframes": {"M15": {"indicators": {
            "ema_fast": 3100.5 + 4.0, "ema_slow": 3100.5 - 4.0,
            "rsi": 55.0, "macd_histogram": 0.8, "bb_pct_b": 0.45,
            "adx": 35.0, "plus_di": 28.0, "minus_di": 18.0, "atr": 10.0,
        }}},
        "current_price": {"bid": 3100.5, "ask": 3101.0},
    }
    hypothesis = await engine.generate_hypothesis(algo_context)
    assert hypothesis is not None, f"algo_ai hypothesis is None: {engine.last_neutral_reason}"

    # Simulate AI review: boost confidence slightly
    boosted = round(min(hypothesis.confidence + 0.05, 0.90), 3)

    async def signal_gen(context, regime):
        return _make_candidate_signal(confidence=boosted)

    result = await _make_full_orchestrator().run(ctx, signal_gen, symbol="XAUUSD", mode="algo_ai")

    assert result.outcome == CycleOutcome.TRADE_OPENED
    assert result.conviction.decision == "TRADE"


@pytest.mark.asyncio
async def test_algo_ai_pipeline_session_filter_blocks():
    """algo_ai: session_filter blocks at MarketGate → REJECTED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal()

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(tools=[_session_block_plugin()]),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_ai")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.market_gate.blocked_by == "session_filter"


@pytest.mark.asyncio
async def test_algo_ai_pipeline_hard_invalidation_rejects():
    """algo_ai: invalid SL → HARD_INVALIDATE → REJECTED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        sig = _make_candidate_signal()
        sig.stop_loss = 3115.0
        return sig

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_ai")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.invalidation.severity == "HARD_INVALIDATE"


@pytest.mark.asyncio
async def test_algo_ai_pipeline_low_conviction_holds():
    """algo_ai: low confidence → conviction HOLD → HELD."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal(confidence=0.35)

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=ConvictionScorer(),
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_ai")

    assert result.outcome == CycleOutcome.HELD
    assert result.conviction.decision == "HOLD"


@pytest.mark.asyncio
async def test_algo_ai_pipeline_risk_gate_blocks():
    """algo_ai: risk monitor blocks (max loss hit) → RiskGate REJECTED."""
    ctx = _make_pipeline_context(can_open_trade=False)

    async def signal_gen(context, regime):
        return _make_candidate_signal()

    scorer = ConvictionScorer()
    scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}
    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=scorer,
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="algo_ai")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.risk_gate.allowed is False


# ---------------------------------------------------------------------------
# ai_signal pipeline tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_signal_pipeline_happy_path():
    """ai_signal: fully AI-constructed signal passes all 8 stages → TRADE_OPENED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal(confidence=0.82)

    result = await _make_full_orchestrator().run(ctx, signal_gen, symbol="XAUUSD", mode="ai_signal")

    assert result.outcome == CycleOutcome.TRADE_OPENED
    assert result.conviction.decision == "TRADE"
    assert result.risk_gate.allowed is True
    assert result.execution_guard.action == "EXECUTE"


@pytest.mark.asyncio
async def test_ai_signal_pipeline_session_filter_blocks():
    """ai_signal: session_filter blocks at MarketGate → REJECTED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal(confidence=0.82)

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(tools=[_session_block_plugin()]),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="ai_signal")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.market_gate.blocked_by == "session_filter"


@pytest.mark.asyncio
async def test_ai_signal_pipeline_hard_invalidation_rejects():
    """ai_signal: AI returns signal with SL on wrong side → HARD_INVALIDATE → REJECTED."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        sig = _make_candidate_signal(confidence=0.82)
        sig.stop_loss = 3115.0   # above entry on a BUY
        return sig

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="ai_signal")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.invalidation.severity == "HARD_INVALIDATE"


@pytest.mark.asyncio
async def test_ai_signal_pipeline_low_conviction_holds():
    """ai_signal: AI returns very low confidence → conviction HOLD → HELD."""
    ctx = _make_pipeline_context()

    async def signal_gen(context, regime):
        return _make_candidate_signal(confidence=0.35)

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=ConvictionScorer(),
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="ai_signal")

    assert result.outcome == CycleOutcome.HELD
    assert result.conviction.decision == "HOLD"


@pytest.mark.asyncio
async def test_ai_signal_pipeline_risk_gate_blocks():
    """ai_signal: risk monitor blocks (max loss hit) → blocks even high-confidence AI signal → REJECTED."""
    ctx = _make_pipeline_context(can_open_trade=False)

    async def signal_gen(context, regime):
        return _make_candidate_signal(confidence=0.90)

    scorer = ConvictionScorer()
    scorer._base_thresholds = {"strong_entry": 75.0, "min_entry": 45.0}
    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={"sl_min_points": 50, "sl_max_points": 3000}),
        conviction_scorer=scorer,
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )
    result = await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode="ai_signal")

    assert result.outcome == CycleOutcome.REJECTED
    assert result.risk_gate.allowed is False
