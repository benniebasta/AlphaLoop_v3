from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from alphaloop.trading.meta_loop import (
    MetaLoop,
    _strategy_version_payload,
    _walk_forward_candidate_payload,
)


def test_strategy_version_payload_includes_strategy_spec_from_active_strategy():
    active = SimpleNamespace(
        tools={"session_filter": True, "news_filter": True},
        validation={"min_confidence": 0.65},
        ai_models={"signal": "gpt-5.4-mini"},
        signal_instruction="Use only strong trends",
        validator_instruction="Reject weak setups",
        strategy_spec=SimpleNamespace(signal_mode="ai_signal"),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        9,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01, "tp1_rr": 1.8},
    )

    assert payload["spec_version"] == "v1"
    assert payload["signal_mode"] == "ai_signal"
    assert payload["strategy_spec"]["spec_version"] == "v1"
    assert payload["strategy_spec"]["signal_mode"] == "ai_signal"
    assert payload["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "Use only strong trends"


def test_strategy_version_payload_prefers_strategy_spec_validator_prompt_bundle():
    active = SimpleNamespace(
        tools={},
        validation={},
        ai_models={},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="ai_signal",
            spec_version="v1",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            metadata={},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        10,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01},
    )

    assert payload["signal_instruction"] == "spec signal"
    assert payload["validator_instruction"] == "spec validator"
    assert payload["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "spec validator"


def test_strategy_version_payload_preserves_conviction_tuning_fields():
    active = SimpleNamespace(
        tools={},
        validation={},
        ai_models={},
        scoring_weights={"trend": 0.45},
        confidence_thresholds={"min_entry": 57.0},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="ai_signal",
            spec_version="v1",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            metadata={},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        11,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01},
    )

    assert payload["scoring_weights"] == {"trend": 0.45}
    assert payload["confidence_thresholds"] == {"min_entry": 57.0}


def test_strategy_version_payload_syncs_ai_models_into_strategy_spec():
    active = SimpleNamespace(
        tools={},
        validation={},
        ai_models={"signal": "stale-signal"},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="ai_signal",
            spec_version="v1",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={},
            ai_models={"signal": "spec-signal"},
            metadata={},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        11,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01},
        overrides={"ai_models": {"signal": "override-signal", "validator": "override-validator"}},
    )

    assert payload["ai_models"] == {
        "signal": "override-signal",
        "validator": "override-validator",
    }
    assert payload["strategy_spec"]["ai_models"] == {
        "signal": "override-signal",
        "validator": "override-validator",
    }


def test_strategy_version_payload_merges_explicit_strategy_spec_with_prompt_and_model_overrides():
    active = SimpleNamespace(
        tools={},
        validation={},
        ai_models={"signal": "stale-signal"},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="algo_ai",
            spec_version="v1",
            setup_family="pullback_continuation",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={"signal_rule_sources": ["ema_crossover"], "signal_logic": "AND"},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={"signal_instruction": "old spec signal"},
            ai_models={"signal": "old-spec-model"},
            metadata={},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        12,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01},
        overrides={
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "direction_model": "ai_hypothesis",
                "entry_model": {"type": "prompt_defined"},
                "prompt_bundle": {"signal_instruction": "explicit spec signal"},
                "ai_models": {"signal": "explicit-spec-model"},
                "metadata": {"source": "ui_ai_signal_card"},
            },
            "signal_instruction": "override signal",
            "validator_instruction": "override validator",
            "ai_models": {"signal": "override-model", "validator": "override-validator"},
        },
    )

    assert payload["signal_mode"] == "ai_signal"
    assert payload["signal_instruction"] == "override signal"
    assert payload["validator_instruction"] == "override validator"
    assert payload["ai_models"] == {
        "signal": "override-model",
        "validator": "override-validator",
    }
    assert payload["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "override signal"
    assert payload["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "override validator"
    assert payload["strategy_spec"]["ai_models"] == {
        "signal": "override-model",
        "validator": "override-validator",
    }


def test_strategy_version_payload_updates_strategy_spec_metadata_for_new_version():
    active = SimpleNamespace(
        tools={},
        validation={},
        ai_models={},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="ai_signal",
            spec_version="v1",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={},
            metadata={"source": "ui_ai_signal_card", "symbol": "OLD", "version": 3},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        12,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01},
    )

    assert payload["source"] == "autolearn"
    assert payload["strategy_spec"]["metadata"]["source"] == "autolearn"
    assert payload["strategy_spec"]["metadata"]["symbol"] == "XAUUSD"
    assert payload["strategy_spec"]["metadata"]["version"] == 12


def test_walk_forward_candidate_payload_preserves_strategy_spec_and_tuning():
    active = SimpleNamespace(
        version=11,
        params={"risk_pct": 0.01, "tp1_rr": 1.5},
        tools={"session_filter": True},
        validation={},
        ai_models={"signal": "gpt-5.4-mini"},
        scoring_weights={"trend": 0.45},
        confidence_thresholds={"min_entry": 57.0},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="ai_signal",
            spec_version="v1",
            setup_family="momentum_expansion",
            direction_model="ai_hypothesis",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            metadata={},
        ),
    )

    payload = _walk_forward_candidate_payload(
        "XAUUSD",
        active,
        {"params": {"risk_pct": 0.02, "tp1_rr": 2.0}},
    )

    assert payload["symbol"] == "XAUUSD"
    assert payload["version"] == 12
    assert payload["params"] == {
        "risk_pct": 0.02,
        "tp1_rr": 2.0,
        "signal_rules": [],
        "signal_logic": "AND",
    }
    assert payload["signal_mode"] == "ai_signal"
    assert payload["scoring_weights"] == {"trend": 0.45}
    assert payload["confidence_thresholds"] == {"min_entry": 57.0}
    assert payload["strategy_spec"]["setup_family"] == "momentum_expansion"
    assert payload["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "spec validator"


def test_walk_forward_candidate_payload_applies_strategy_shape_overrides():
    active = SimpleNamespace(
        version=4,
        params={"risk_pct": 0.01, "signal_rules": [{"source": "ema_crossover"}]},
        tools={"session_filter": True},
        validation={},
        ai_models={},
        scoring_weights={"trend": 0.45},
        confidence_thresholds={"min_entry": 57.0},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        source="legacy_source",
        strategy_spec=SimpleNamespace(
            signal_mode="algo_ai",
            spec_version="v1",
            setup_family="pullback_continuation",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            metadata={"source": "legacy_source", "symbol": "OLD", "version": 4},
        ),
    )

    payload = _walk_forward_candidate_payload(
        "XAUUSD",
        active,
        {
            "params": {"risk_pct": 0.02, "signal_rules": [{"source": "ema_crossover"}]},
            "tools": ["fast_fingers"],
            "source": "autolearn",
            "signal_instruction": "new spec signal",
        },
    )

    assert payload["version"] == 5
    assert payload["source"] == "autolearn"
    assert payload["setup_family"] == "momentum_expansion"
    assert payload["tools"] == {"fast_fingers": True}
    assert payload["strategy_spec"]["setup_family"] == "momentum_expansion"
    assert payload["strategy_spec"]["metadata"]["source"] == "autolearn"
    assert payload["strategy_spec"]["metadata"]["symbol"] == "XAUUSD"
    assert payload["strategy_spec"]["metadata"]["version"] == 5
    assert payload["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "new spec signal"


def test_walk_forward_candidate_payload_rebuilds_params_from_spec_entry_model():
    active = SimpleNamespace(
        version=4,
        params={"risk_pct": 0.01, "signal_rules": [{"source": "ema_crossover"}], "signal_logic": "AND"},
        tools={},
        validation={},
        ai_models={},
        scoring_weights={},
        confidence_thresholds={},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        source="legacy_source",
        strategy_spec=SimpleNamespace(
            signal_mode="algo_ai",
            spec_version="v1",
            setup_family="momentum_expansion",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={},
            metadata={"source": "legacy_source", "symbol": "OLD", "version": 4},
        ),
    )

    payload = _walk_forward_candidate_payload(
        "XAUUSD",
        active,
        {"params": {"risk_pct": 0.02, "signal_rules": [{"source": "ema_crossover"}], "signal_logic": "AND"}},
    )

    assert payload["params"]["risk_pct"] == 0.02
    assert payload["params"]["signal_rules"] == [{"source": "macd_crossover"}]
    assert payload["params"]["signal_logic"] == "OR"


def test_strategy_version_payload_applies_overrides_before_serializing_spec():
    active = SimpleNamespace(
        tools={"session_filter": True},
        validation={},
        ai_models={},
        scoring_weights={"trend": 0.45},
        confidence_thresholds={"min_entry": 57.0},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="algo_ai",
            spec_version="v1",
            setup_family="pullback_continuation",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            metadata={"source": "legacy_source", "symbol": "OLD", "version": 3},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        13,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01, "signal_rules": [{"source": "ema_crossover"}]},
        overrides={
            "tools": ["bos_guard"],
            "signal_instruction": "override signal",
        },
    )

    assert payload["source"] == "autolearn"
    assert payload["setup_family"] == "breakout_retest"
    assert payload["strategy_spec"]["setup_family"] == "breakout_retest"
    assert payload["strategy_spec"]["metadata"]["source"] == "autolearn"
    assert payload["strategy_spec"]["metadata"]["version"] == 13
    assert payload["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "override signal"


def test_strategy_version_payload_rebuilds_params_from_spec_entry_model():
    active = SimpleNamespace(
        tools={},
        validation={},
        ai_models={},
        scoring_weights={},
        confidence_thresholds={},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="algo_ai",
            spec_version="v1",
            setup_family="momentum_expansion",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={},
            metadata={"source": "legacy_source", "symbol": "OLD", "version": 3},
        ),
    )

    payload = _strategy_version_payload(
        "XAUUSD",
        14,
        "candidate",
        "autolearn",
        active,
        {"risk_pct": 0.01, "signal_rules": [{"source": "ema_crossover"}], "signal_logic": "AND"},
    )

    assert payload["params"]["risk_pct"] == 0.01
    assert payload["params"]["signal_rules"] == [{"source": "macd_crossover"}]
    assert payload["params"]["signal_logic"] == "OR"


async def test_meta_loop_create_strategy_version_uses_reserved_next_version(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import alphaloop.backtester.asset_trainer as asset_trainer

    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    existing = tmp_path / "XAUUSD_v2.json"
    existing.write_text(json.dumps({"symbol": "XAUUSD", "version": 2}))

    event_bus = SimpleNamespace(publish=AsyncMock())
    loop = MetaLoop(
        symbol="XAUUSD",
        session_factory=None,
        event_bus=event_bus,
        settings_service=SimpleNamespace(),
    )

    active = SimpleNamespace(
        version=1,
        tools={},
        validation={},
        ai_models={},
        scoring_weights={},
        confidence_thresholds={},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="ai_signal",
            spec_version="v1",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={},
            metadata={},
        ),
    )

    new_version = await loop._create_strategy_version(active, {"params": {"risk_pct": 0.01}})

    assert new_version == 3
    assert json.loads(existing.read_text()) == {"symbol": "XAUUSD", "version": 2}
    written = json.loads((tmp_path / "XAUUSD_v3.json").read_text())
    assert written["version"] == 3
    assert written["source"] == "autolearn"
    event_bus.publish.assert_awaited()


async def test_meta_loop_execute_rollback_uses_canonical_active_strategy_payload(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import alphaloop.trading.meta_loop as meta_loop

    monkeypatch.setattr(meta_loop, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    (tmp_path / "XAUUSD_v4.json").write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 4,
        "status": "dry_run",
        "signal_mode": "algo_only",
        "summary": {"sharpe_ratio": 1.3},
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
    }))

    settings_service = SimpleNamespace(set=AsyncMock())
    event_bus = SimpleNamespace(publish=AsyncMock())
    loop = MetaLoop(
        symbol="XAUUSD",
        instance_id="inst-1",
        session_factory=None,
        event_bus=event_bus,
        settings_service=settings_service,
    )
    loop._rollback_tracker = SimpleNamespace(previous_version=4)

    await loop._execute_rollback()

    assert settings_service.set.await_count == 2
    instance_payload = json.loads(settings_service.set.await_args_list[0].args[1])
    assert instance_payload["signal_mode"] == "ai_signal"
    assert instance_payload["signal_instruction"] == "spec signal"
    assert instance_payload["validator_instruction"] == "spec validator"
    assert instance_payload["summary"]["sharpe"] == 1.3
    assert instance_payload["strategy_spec"]["signal_mode"] == "ai_signal"
    assert instance_payload["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"
    assert loop._rollback_tracker is None


async def test_run_walk_forward_gate_accepts_oos_max_dd_pct_and_persists_oos_metrics(monkeypatch):
    import alphaloop.backtester.runner as runner_module

    class FakeRunner:
        def __init__(self, session_factory=None):
            self.session_factory = session_factory

        def run_walk_forward(self, *, strategy, symbol, total_days, oos_days):
            assert symbol == "XAUUSD"
            assert total_days == 90
            assert oos_days == 30
            return {"oos_sharpe": 0.5, "oos_max_dd_pct": 0.15}

    monkeypatch.setattr(runner_module, "BacktestRunner", FakeRunner, raising=False)

    loop = MetaLoop(
        symbol="XAUUSD",
        session_factory=None,
        event_bus=SimpleNamespace(publish=AsyncMock()),
        settings_service=SimpleNamespace(),
    )
    active = SimpleNamespace(
        version=4,
        params={"risk_pct": 0.01},
        tools={},
        validation={},
        ai_models={},
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        strategy_spec=SimpleNamespace(
            signal_mode="algo_ai",
            spec_version="v1",
            setup_family="pullback_continuation",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            prompt_bundle={},
            metadata={},
        ),
    )
    improved_params = {"params": {"risk_pct": 0.02}}

    ok = await loop._run_walk_forward_gate(active, improved_params)

    assert ok is True
    assert improved_params["oos_metrics"] == {
        "sharpe": 0.5,
        "max_dd_pct": 0.15,
        "max_drawdown": 0.15,
    }
