"""Unit tests for the Gate-1 TradeDecision projection.

These tests cover ``pipeline.types.build_trade_decision`` — the function the
UI and observability ledger rely on to render every cycle. They use plain
dataclasses (no DB, no broker) to verify that penalties, reject stage, and
execution status are extracted correctly from a `PipelineResult`-shaped
object.
"""

from __future__ import annotations

from types import SimpleNamespace

from alphaloop.pipeline.types import (
    CandidateJourney,
    ConvictionScore,
    CycleOutcome,
    InvalidationResult,
    SizingDecision,
    TradeDecision,
    build_trade_decision,
)


def _journey_with_stages(*stages: tuple[str, str, dict | None]) -> CandidateJourney:
    j = CandidateJourney()
    for stage, status, kwargs in stages:
        j.add_stage(stage, status, **(kwargs or {}))
    return j


def _make_result(**overrides):
    """Build a minimal PipelineResult-shaped object."""
    return SimpleNamespace(
        outcome=overrides.get("outcome", CycleOutcome.TRADE_OPENED),
        market_gate=overrides.get("market_gate"),
        regime=overrides.get("regime"),
        hypothesis=overrides.get("hypothesis"),
        signal=overrides.get("signal"),
        invalidation=overrides.get("invalidation"),
        quality=overrides.get("quality"),
        conviction=overrides.get("conviction"),
        risk_gate=overrides.get("risk_gate"),
        execution_guard=overrides.get("execution_guard"),
        sizing=overrides.get("sizing"),
        elapsed_ms=overrides.get("elapsed_ms", 12.5),
        rejection_reason=overrides.get("rejection_reason"),
        construction_source=overrides.get("construction_source"),
        journey=overrides.get("journey") or CandidateJourney(),
    )


def test_trade_decision_projects_trade_opened_cycle():
    signal = SimpleNamespace(direction="BUY", setup_type="pullback", raw_confidence=0.72)
    conviction = ConvictionScore(
        score=78.0,
        normalized=0.78,
        size_scalar=1.0,
        decision="TRADE",
    )
    sizing = SizingDecision(
        conviction_scalar=1.0,
        regime_scalar=0.9,
        freshness_scalar=1.0,
        risk_gate_scalar=1.0,
        equity_curve_scalar=1.0,
    )
    journey = _journey_with_stages(
        ("market_gate", "passed", None),
        ("regime", "classified", None),
        ("signal", "hypothesis_generated", None),
        ("construction", "constructed", None),
        ("invalidation", "passed", None),
        ("quality", "scored", None),
        ("conviction", "trade", None),
        ("ai_validator", "skipped", None),
        ("risk_gate", "passed", None),
        ("execution_guard", "execute", None),
        ("sizing", "computed", None),
    )
    result = _make_result(
        signal=signal,
        conviction=conviction,
        sizing=sizing,
        journey=journey,
    )
    result.outcome = CycleOutcome.TRADE_OPENED  # enum, not string

    decision = build_trade_decision(result, symbol="XAUUSD", mode="algo_only")

    assert isinstance(decision, TradeDecision)
    assert decision.outcome == "trade_opened"
    assert decision.execution_status == "executed"
    assert decision.direction == "BUY"
    assert decision.setup_type == "pullback"
    assert decision.conviction_decision == "TRADE"
    assert decision.reject_stage is None
    assert decision.ai_verdict == "skipped"
    assert decision.hard_block is False
    # size multiplier = product of all five scalars
    assert round(decision.size_multiplier, 4) == round(1.0 * 0.9 * 1.0 * 1.0 * 1.0, 4)
    # No penalties on a clean cycle
    assert decision.penalties == []


def test_trade_decision_captures_conviction_hold_with_penalties():
    conviction = ConvictionScore(
        score=48.0,
        normalized=0.48,
        size_scalar=0.0,
        decision="HOLD",
        hold_reason="conviction below entry threshold",
        invalidation_penalty=8.0,
        conflict_penalty=5.0,
        portfolio_penalty=3.0,
        total_penalty=16.0,
        penalties_prorated=False,
    )
    journey = _journey_with_stages(
        ("market_gate", "passed", None),
        ("regime", "classified", None),
        ("signal", "signal_generated", None),
        ("invalidation", "soft_invalidated", None),
        ("quality", "scored", None),
        ("conviction", "held", {"blocked_by": "conviction",
                                "detail": "conviction below entry threshold"}),
    )
    signal = SimpleNamespace(direction="SELL", setup_type="breakout", raw_confidence=0.55)
    result = _make_result(
        outcome=CycleOutcome.HELD,
        signal=signal,
        conviction=conviction,
        journey=journey,
        rejection_reason="conviction below entry threshold",
    )

    decision = build_trade_decision(result, symbol="XAUUSD", mode="algo_ai")

    assert decision.outcome == "held"
    assert decision.reject_stage == "conviction"
    assert decision.conviction_decision == "HOLD"
    assert decision.execution_status == "blocked"
    assert decision.hard_block is False  # conviction is a soft blocker
    sources = {p["source"] for p in decision.penalties}
    assert {"invalidation", "conflict", "portfolio"}.issubset(sources)


def test_trade_decision_marks_hard_block_on_invalidation_rejection():
    inv = InvalidationResult(
        severity="HARD_INVALIDATE",
        failures=[],
        conviction_penalty=0.0,
        checks_run=["sl_direction"],
    )
    journey = _journey_with_stages(
        ("market_gate", "passed", None),
        ("regime", "classified", None),
        ("signal", "signal_generated", None),
        ("invalidation", "hard_invalidated", {"blocked_by": "invalidation"}),
    )
    signal = SimpleNamespace(direction="BUY", setup_type="pullback", raw_confidence=0.65)
    result = _make_result(
        outcome=CycleOutcome.REJECTED,
        signal=signal,
        invalidation=inv,
        journey=journey,
        rejection_reason="SL on wrong side of entry",
    )

    decision = build_trade_decision(result, symbol="BTCUSD", mode="ai_signal")

    assert decision.outcome == "rejected"
    assert decision.reject_stage == "invalidation"
    assert decision.hard_block is True
    assert decision.execution_status == "blocked"


def test_trade_decision_ai_verdict_reject_flips_execution():
    journey = _journey_with_stages(
        ("market_gate", "passed", None),
        ("regime", "classified", None),
        ("signal", "signal_generated", None),
        ("invalidation", "passed", None),
        ("quality", "scored", None),
        ("conviction", "trade", None),
        ("ai_validator", "rejected", {"blocked_by": "ai_validator",
                                       "detail": "contradicts structural read"}),
    )
    signal = SimpleNamespace(direction="SELL", setup_type="reversal", raw_confidence=0.80)
    result = _make_result(
        outcome=CycleOutcome.REJECTED,
        signal=signal,
        journey=journey,
        rejection_reason="AI validator rejected",
    )

    decision = build_trade_decision(result, symbol="EURUSD", mode="ai_signal")

    assert decision.outcome == "rejected"
    assert decision.ai_verdict == "reject"
    assert decision.reject_stage == "ai_validator"
    assert decision.hard_block is True


def test_trade_decision_no_signal_is_held_not_hard_block():
    journey = _journey_with_stages(
        ("market_gate", "passed", None),
        ("regime", "classified", None),
        ("signal", "no_signal", None),
    )
    result = _make_result(
        outcome=CycleOutcome.NO_SIGNAL,
        journey=journey,
    )

    decision = build_trade_decision(result, symbol="XAUUSD", mode="algo_only")

    assert decision.outcome == "no_signal"
    assert decision.execution_status == "none"
    assert decision.hard_block is False
    # reject_stage should be 'signal' — the last journey stage with a no_signal status
    assert decision.reject_stage == "signal"


def test_trade_decision_to_dict_round_trips_penalties_and_journey():
    signal = SimpleNamespace(direction="BUY", setup_type="continuation", raw_confidence=0.66)
    conviction = ConvictionScore(
        score=62.0, normalized=0.62, size_scalar=0.6,
        decision="TRADE",
        invalidation_penalty=4.0,
    )
    journey = _journey_with_stages(
        ("market_gate", "passed", None),
        ("signal", "signal_generated", None),
        ("conviction", "trade", None),
    )
    result = _make_result(
        outcome=CycleOutcome.TRADE_OPENED,
        signal=signal,
        conviction=conviction,
        journey=journey,
        sizing=SizingDecision(),
    )

    decision = build_trade_decision(result, symbol="XAUUSD", mode="algo_ai")
    payload = decision.to_dict()

    assert payload["symbol"] == "XAUUSD"
    assert payload["mode"] == "algo_ai"
    assert payload["outcome"] == "trade_opened"
    assert payload["conviction_score"] == 62.0
    assert any(p["source"] == "invalidation" for p in payload["penalties"])
    assert payload["journey"] is not None
    assert len(payload["journey"]["stages"]) == 3
