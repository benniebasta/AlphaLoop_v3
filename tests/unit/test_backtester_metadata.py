from __future__ import annotations

import json

import pytest

import alphaloop.backtester.asset_trainer as asset_trainer
from alphaloop.backtester.optimizer import suggest_params
from alphaloop.backtester.params import BacktestParams
from alphaloop.backtester.runner import (
    _base_backtest_params,
    _load_checkpoint,
    _save_checkpoint,
    _strategy_version_write_kwargs,
)


class _Trial:
    def suggest_float(self, name, low, high):
        return (low + high) / 2.0

    def suggest_int(self, name, low, high):
        return low

    def suggest_categorical(self, name, choices):
        return choices[0]


def test_suggest_params_preserves_strategy_metadata():
    base = BacktestParams(
        signal_mode="ai_signal",
        setup_family="momentum_expansion",
        strategy_spec={"setup_family": "momentum_expansion", "signal_mode": "ai_signal"},
        tools={"session_filter": True},
        source="meta_loop",
    )

    params = suggest_params(_Trial(), base)

    assert params.signal_mode == "ai_signal"
    assert params.setup_family == "momentum_expansion"
    assert params.strategy_spec["setup_family"] == "momentum_expansion"
    assert params.tools == {"session_filter": True}
    assert params.source == "meta_loop"


def test_suggest_params_prefers_strategy_spec_metadata_over_stale_flat_fields():
    base = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "setup_family": "discretionary_ai",
            "signal_mode": "ai_signal",
            "metadata": {"source": "ui_ai_signal_card"},
        },
        tools={"session_filter": True},
        source="legacy_flat_source",
    )

    params = suggest_params(_Trial(), base)

    assert params.signal_mode == "ai_signal"
    assert params.setup_family == "discretionary_ai"
    assert params.source == "ui_ai_signal_card"


def test_suggest_params_prefers_strategy_spec_entry_model_rules_and_logic():
    base = BacktestParams(
        signal_mode="algo_ai",
        setup_family="momentum_expansion",
        strategy_spec={
            "setup_family": "momentum_expansion",
            "signal_mode": "algo_ai",
            "entry_model": {
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        },
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
    )

    params = suggest_params(_Trial(), base)

    assert params.signal_rules == [{"source": "macd_crossover"}]
    assert params.signal_logic == "OR"


def test_serialize_best_params_prefers_spec_first_identity_prompts_and_models():
    params = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            "ai_models": {
                "signal": "spec-signal-model",
                "validator": "spec-validator-model",
            },
            "metadata": {"source": "ui_ai_signal_card"},
        },
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
        tools={"fast_fingers": True},
        source="legacy_source",
    )

    serialized = asset_trainer._serialize_best_params(params)

    assert serialized["spec_version"] == "v1"
    assert serialized["signal_mode"] == "ai_signal"
    assert serialized["setup_family"] == "discretionary_ai"
    assert serialized["source"] == "ui_ai_signal_card"
    assert serialized["signal_instruction"] == "spec signal"
    assert serialized["validator_instruction"] == "spec validator"
    assert serialized["ai_models"] == {
        "signal": "spec-signal-model",
        "validator": "spec-validator-model",
    }
    assert serialized["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_checkpoint_round_trip_preserves_strategy_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    params = BacktestParams(
        signal_mode="ai_signal",
        setup_family="discretionary_ai",
        strategy_spec={"setup_family": "discretionary_ai", "signal_mode": "ai_signal"},
        signal_rules=[{"source": "ema_crossover"}, {"source": "rsi_reclaim"}],
        signal_logic="OR",
        signal_auto=True,
        tools={"news_filter": True},
        source="backtest_runner",
    )

    _save_checkpoint("run-1", 3, params, 1.23, "abc123")
    restored, generation, sharpe = _load_checkpoint("run-1", "abc123")

    assert restored is not None
    assert generation == 3
    assert sharpe == 1.23
    assert restored.signal_mode == "ai_signal"
    assert restored.setup_family == "discretionary_ai"
    assert restored.strategy_spec["setup_family"] == "discretionary_ai"
    assert restored.signal_rules == [{"source": "ema_crossover"}, {"source": "rsi_reclaim"}]
    assert restored.signal_logic == "OR"
    assert restored.signal_auto is True
    assert restored.tools == {"news_filter": True}
    assert restored.source == "backtest_runner"
    assert restored.strategy_spec["metadata"]["source"] == "backtest_runner"


def test_strategy_version_write_kwargs_prefer_best_params_metadata():
    params = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "metadata": {"source": "ui_ai_signal_card"},
        },
        tools={"fast_fingers": True},
        source="legacy_source",
    )

    kwargs = _strategy_version_write_kwargs(
        params=params,
        metrics={"sharpe": 1.0},
        tools=["bos_guard"],
        source="backtest_runner",
        name="run-name",
        timeframe="1h",
        days=30,
        initial_capital=10_000.0,
    )

    assert kwargs["signal_mode"] == "ai_signal"
    assert kwargs["source"] == "ui_ai_signal_card"
    assert kwargs["tools"] == ["fast_fingers"]


def test_checkpoint_round_trip_prefers_strategy_spec_entry_model_rules_and_logic(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    params = BacktestParams(
        signal_mode="algo_ai",
        setup_family="momentum_expansion",
        strategy_spec={
            "setup_family": "momentum_expansion",
            "signal_mode": "algo_ai",
            "entry_model": {
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        },
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
    )

    _save_checkpoint("run-entry-model", 1, params, 1.0, "entry123")
    restored, generation, sharpe = _load_checkpoint("run-entry-model", "entry123")

    assert restored is not None
    assert generation == 1
    assert sharpe == 1.0
    assert restored.signal_rules == [{"source": "macd_crossover"}]
    assert restored.signal_logic == "OR"


def test_checkpoint_round_trip_preserves_none_signal_rules_as_default_ema(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    params = BacktestParams.model_construct(
        signal_mode="algo_ai",
        setup_family="",
        strategy_spec={},
        signal_rules=None,
        signal_logic="AND",
        signal_auto=False,
        max_param_change_pct=0.15,
        tools={},
        source="legacy_source",
        ema_fast=21,
        ema_slow=55,
        sl_atr_mult=2.0,
        tp1_rr=2.0,
        tp2_rr=4.0,
        rsi_period=14,
        rsi_ob=70.0,
        rsi_os=30.0,
        risk_pct=0.01,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        bb_period=20,
        bb_std_dev=2.0,
        adx_period=14,
        adx_min_threshold=20.0,
        volume_ma_period=20,
    )

    _save_checkpoint("run-none-rules", 2, params, 0.75, "none123")
    restored, generation, sharpe = _load_checkpoint("run-none-rules", "none123")

    assert restored is not None
    assert generation == 2
    assert sharpe == 0.75
    assert restored.signal_rules == [{"source": "ema_crossover"}]
    assert restored.setup_family == "trend_continuation"
    assert restored.strategy_spec["setup_family"] == "trend_continuation"


def test_checkpoint_round_trip_prefers_strategy_spec_signal_mode_and_family(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    params = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={"setup_family": "discretionary_ai", "signal_mode": "ai_signal"},
        tools={"news_filter": True},
        source="backtest_runner",
    )

    _save_checkpoint("run-2", 1, params, 0.55, "def456")
    restored, generation, sharpe = _load_checkpoint("run-2", "def456")

    assert restored is not None
    assert generation == 1
    assert sharpe == 0.55
    assert restored.signal_mode == "ai_signal"
    assert restored.setup_family == "discretionary_ai"


def test_checkpoint_round_trip_prefers_strategy_spec_metadata_source(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    params = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "setup_family": "discretionary_ai",
            "signal_mode": "ai_signal",
            "metadata": {"source": "ui_ai_signal_card"},
        },
        source="legacy_flat_source",
    )

    _save_checkpoint("run-3", 1, params, 0.8, "ghi789")
    restored, generation, sharpe = _load_checkpoint("run-3", "ghi789")

    assert restored is not None
    assert generation == 1
    assert sharpe == 0.8
    assert restored.source == "ui_ai_signal_card"
    assert restored.strategy_spec["metadata"]["source"] == "ui_ai_signal_card"


def test_save_checkpoint_persists_canonical_spec_version_and_source(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )
    params = BacktestParams(
        signal_mode="algo_only",
        setup_family="pullback_continuation",
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "metadata": {"source": "ui_ai_signal_card"},
        },
        source="legacy_flat_source",
    )

    _save_checkpoint("run-spec-source", 2, params, 0.75, "specsrc123")

    payload = json.loads((tmp_path / "run-spec-source.json").read_text())
    assert payload["best_params"]["spec_version"] == "v1"
    assert payload["best_params"]["source"] == "ui_ai_signal_card"
    assert payload["best_params"]["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_checkpoint_round_trip_infers_family_from_tools_when_flat_family_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    params = BacktestParams(
        signal_mode="algo_ai",
        setup_family="pullback_continuation",
        strategy_spec={},
        signal_rules=[],
        signal_logic="AND",
        tools={"fast_fingers": True},
        source="backtest_runner",
    )

    _save_checkpoint("run-4", 1, params, 0.9, "jkl012")
    restored, generation, sharpe = _load_checkpoint("run-4", "jkl012")

    assert restored is not None
    assert generation == 1
    assert sharpe == 0.9
    assert restored.setup_family == "momentum_expansion"


def test_load_checkpoint_normalizes_list_style_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    payload = {
        "run_id": "run-legacy-tools",
        "generation": 2,
        "best_sharpe": 1.1,
        "data_hash": "legacy123",
        "saved_at": "2026-01-01T00:00:00+00:00",
        "best_params": {
            "ema_fast": 21,
            "ema_slow": 55,
            "sl_atr_mult": 2.0,
            "tp1_rr": 2.0,
            "tp2_rr": 4.0,
            "rsi_ob": 70,
            "rsi_os": 30,
            "rsi_period": 14,
            "risk_pct": 0.01,
            "signal_rules": [],
            "signal_logic": "AND",
            "signal_auto": False,
            "max_param_change_pct": 0.15,
            "signal_mode": "algo_ai",
            "setup_family": "pullback_continuation",
            "strategy_spec": {},
            "tools": ["fast_fingers"],
            "source": "legacy_source",
        },
    }
    (tmp_path / "run-legacy-tools.json").write_text(json.dumps(payload, indent=2))

    restored, generation, sharpe = _load_checkpoint("run-legacy-tools", "legacy123")

    assert restored is not None
    assert generation == 2
    assert sharpe == 1.1
    assert restored.tools == {"fast_fingers": True}
    assert restored.setup_family == "momentum_expansion"


def test_load_checkpoint_defaults_none_signal_rules_for_backward_compat(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "alphaloop.backtester.runner._CHECKPOINT_DIR",
        tmp_path,
    )

    payload = {
        "run_id": "run-legacy-rules",
        "generation": 2,
        "best_sharpe": 1.1,
        "data_hash": "legacy456",
        "saved_at": "2026-01-01T00:00:00+00:00",
        "best_params": {
            "ema_fast": 21,
            "ema_slow": 55,
            "sl_atr_mult": 2.0,
            "tp1_rr": 2.0,
            "tp2_rr": 4.0,
            "rsi_ob": 70,
            "rsi_os": 30,
            "rsi_period": 14,
            "risk_pct": 0.01,
            "signal_rules": None,
            "signal_logic": None,
            "signal_auto": False,
            "max_param_change_pct": 0.15,
            "signal_mode": "algo_ai",
            "setup_family": "pullback_continuation",
            "strategy_spec": {},
            "tools": {},
            "source": "legacy_source",
        },
    }
    (tmp_path / "run-legacy-rules.json").write_text(json.dumps(payload, indent=2))

    restored, generation, sharpe = _load_checkpoint("run-legacy-rules", "legacy456")

    assert restored is not None
    assert generation == 2
    assert sharpe == 1.1
    assert restored.signal_rules == [{"source": "ema_crossover"}]
    assert restored.signal_logic == "AND"
    assert restored.setup_family == "trend_continuation"


def test_base_backtest_params_infers_family_from_tools_and_rules():
    params = _base_backtest_params(
        signal_mode="algo_ai",
        signal_rules=[],
        signal_logic="AND",
        signal_auto=False,
        tools=["fast_fingers"],
    )

    assert params.signal_mode == "algo_ai"
    assert params.setup_family == "momentum_expansion"
    assert params.tools == {"fast_fingers": True}
    assert params.source == "backtest_runner"
    assert params.strategy_spec["metadata"]["source"] == "backtest_runner"


@pytest.mark.asyncio
async def test_train_from_card_prefers_strategy_spec_signal_mode_and_family(monkeypatch):
    class _Result:
        trade_count = 3
        win_rate = 0.66
        sharpe = 1.25
        max_drawdown_pct = 2.0
        total_pnl = 150.0

    class _WFResult:
        passes_gate = True
        gate_reason = "ok"

        def summary(self):
            return {"passed": True, "reason": "ok"}

    captured: dict[str, object] = {}

    async def _fake_fetch_data(*args, **kwargs):
        return [1.0, 2.0], [1.1, 2.1], [0.9, 1.9], [1.05, 2.05], [1, 2]

    def _fake_run_vbt(symbol, opens, highs, lows, closes, timestamps, balance, params):
        captured["baseline_signal_mode"] = params.signal_mode
        captured["baseline_setup_family"] = params.setup_family
        captured["baseline_strategy_spec"] = params.strategy_spec
        return _Result()

    def _fake_create_strategy_version(**kwargs):
        captured["version_signal_mode"] = kwargs["signal_mode"]
        captured["version_params_signal_mode"] = kwargs["params"].signal_mode
        captured["version_params_setup_family"] = kwargs["params"].setup_family
        captured["version_params_strategy_spec"] = kwargs["params"].strategy_spec
        return {"_version": 1}

    monkeypatch.setattr(asset_trainer, "_fetch_data", _fake_fetch_data)
    monkeypatch.setattr(asset_trainer, "_run_vbt", _fake_run_vbt)
    monkeypatch.setattr(asset_trainer, "create_strategy_version", _fake_create_strategy_version)
    monkeypatch.setattr(
        "alphaloop.backtester.walk_forward.run_walk_forward",
        lambda *args, **kwargs: _WFResult(),
    )

    result = await asset_trainer.train_from_card(
        {
            "name": "Spec Card",
            "filters": [],
            "params": {},
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
            },
        },
        symbol="XAUUSD",
        days=2,
        max_generations=1,
    )

    assert result["success"] is True
    assert captured["baseline_signal_mode"] == "ai_signal"
    assert captured["baseline_setup_family"] == "discretionary_ai"
    assert captured["baseline_strategy_spec"]["signal_mode"] == "ai_signal"
    assert captured["baseline_strategy_spec"]["setup_family"] == "discretionary_ai"
    assert captured["version_signal_mode"] == "ai_signal"
    assert captured["version_params_signal_mode"] == "ai_signal"
    assert captured["version_params_setup_family"] == "discretionary_ai"
    assert captured["version_params_strategy_spec"]["signal_mode"] == "ai_signal"
    assert result["best_params"]["signal_mode"] == "ai_signal"
    assert result["best_params"]["setup_family"] == "discretionary_ai"
    assert result["best_params"]["strategy_spec"]["signal_mode"] == "ai_signal"


@pytest.mark.asyncio
async def test_train_from_card_prefers_strategy_spec_entry_model_rules_and_logic(monkeypatch):
    class _Result:
        trade_count = 3
        win_rate = 0.66
        sharpe = 1.2
        max_drawdown_pct = 2.0
        total_pnl = 150.0

    class _WFResult:
        passes_gate = True
        gate_reason = "ok"

        def summary(self):
            return {"passed": True, "reason": "ok"}

    captured: dict[str, object] = {}

    async def _fake_fetch_data(*args, **kwargs):
        import numpy as np
        return np.arange(100), np.arange(100), np.arange(100), np.arange(100), list(range(100))

    def _fake_run_vbt(symbol, opens, highs, lows, closes, timestamps, balance, params):
        captured["baseline_signal_rules"] = params.signal_rules
        captured["baseline_signal_logic"] = params.signal_logic
        return _Result()

    def _fake_create_strategy_version(**kwargs):
        captured["version_params_signal_rules"] = kwargs["params"].signal_rules
        captured["version_params_signal_logic"] = kwargs["params"].signal_logic
        return {"_version": 1}

    monkeypatch.setattr(asset_trainer, "_fetch_data", _fake_fetch_data)
    monkeypatch.setattr(asset_trainer, "_run_vbt", _fake_run_vbt)
    monkeypatch.setattr(asset_trainer, "create_strategy_version", _fake_create_strategy_version)
    monkeypatch.setattr(
        "alphaloop.backtester.optimizer.optimize",
        lambda *args, **kwargs: (kwargs.get("base_params") or args[0], 1.2, False),
    )
    monkeypatch.setattr(
        "alphaloop.backtester.walk_forward.run_walk_forward",
        lambda *args, **kwargs: _WFResult(),
    )

    result = await asset_trainer.train_from_card(
        {
            "name": "Spec Card",
            "filters": [],
            "params": {
                "signal_rules": [{"source": "ema_crossover"}],
                "signal_logic": "AND",
            },
            "signal_mode": "algo_ai",
            "strategy_spec": {
                "signal_mode": "algo_ai",
                "setup_family": "momentum_expansion",
                "entry_model": {
                    "signal_rule_sources": ["macd_crossover"],
                    "signal_logic": "OR",
                },
            },
        },
        symbol="XAUUSD",
        max_generations=1,
    )

    assert result["success"] is True
    assert captured["baseline_signal_rules"] == [{"source": "macd_crossover"}]
    assert captured["baseline_signal_logic"] == "OR"
    assert captured["version_params_signal_rules"] == [{"source": "macd_crossover"}]
    assert captured["version_params_signal_logic"] == "OR"
    assert result["best_params"]["signal_rules"] == [{"source": "macd_crossover"}]
    assert result["best_params"]["signal_logic"] == "OR"
    assert result["best_params"]["strategy_spec"]["entry_model"]["signal_logic"] == "OR"


@pytest.mark.asyncio
async def test_train_from_card_prefers_strategy_spec_metadata_source(monkeypatch):
    class _Result:
        trade_count = 3
        win_rate = 0.66
        sharpe = 1.25
        max_drawdown_pct = 2.0
        total_pnl = 150.0

    class _WFResult:
        passes_gate = True
        gate_reason = "ok"

        def summary(self):
            return {"passed": True, "reason": "ok"}

    captured: dict[str, object] = {}

    async def _fake_fetch_data(*args, **kwargs):
        return [1.0, 2.0], [1.1, 2.1], [0.9, 1.9], [1.05, 2.05], [1, 2]

    def _fake_run_vbt(symbol, opens, highs, lows, closes, timestamps, balance, params):
        captured["baseline_source"] = params.source
        return _Result()

    def _fake_create_strategy_version(**kwargs):
        captured["version_source"] = kwargs["source"]
        captured["version_params_source"] = kwargs["params"].source
        return {"_version": 1}

    monkeypatch.setattr(asset_trainer, "_fetch_data", _fake_fetch_data)
    monkeypatch.setattr(asset_trainer, "_run_vbt", _fake_run_vbt)
    monkeypatch.setattr(asset_trainer, "create_strategy_version", _fake_create_strategy_version)
    monkeypatch.setattr(
        "alphaloop.backtester.walk_forward.run_walk_forward",
        lambda *args, **kwargs: _WFResult(),
    )

    result = await asset_trainer.train_from_card(
        {
            "name": "Spec Card",
            "filters": [],
            "params": {},
            "source": "legacy_flat_source",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        },
        symbol="XAUUSD",
        days=2,
        max_generations=1,
    )

    assert result["success"] is True
    assert captured["baseline_source"] == "ui_ai_signal_card"
    assert captured["version_source"] == "ui_ai_signal_card"
    assert captured["version_params_source"] == "ui_ai_signal_card"
    assert result["best_params"]["source"] == "ui_ai_signal_card"
    assert result["best_params"]["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


@pytest.mark.asyncio
async def test_train_from_card_infers_setup_family_from_filters_when_flat_family_is_stale(monkeypatch):
    class _Result:
        trade_count = 3
        win_rate = 0.66
        sharpe = 1.25
        max_drawdown_pct = 2.0
        total_pnl = 150.0

    class _WFResult:
        passes_gate = True
        gate_reason = "ok"

        def summary(self):
            return {"passed": True, "reason": "ok"}

    captured: dict[str, object] = {}

    async def _fake_fetch_data(*args, **kwargs):
        return [1.0, 2.0], [1.1, 2.1], [0.9, 1.9], [1.05, 2.05], [1, 2]

    def _fake_run_vbt(symbol, opens, highs, lows, closes, timestamps, balance, params):
        captured["baseline_setup_family"] = params.setup_family
        return _Result()

    def _fake_create_strategy_version(**kwargs):
        captured["version_params_setup_family"] = kwargs["params"].setup_family
        return {"_version": 1}

    monkeypatch.setattr(asset_trainer, "_fetch_data", _fake_fetch_data)
    monkeypatch.setattr(asset_trainer, "_run_vbt", _fake_run_vbt)
    monkeypatch.setattr(asset_trainer, "create_strategy_version", _fake_create_strategy_version)
    monkeypatch.setattr(
        "alphaloop.backtester.walk_forward.run_walk_forward",
        lambda *args, **kwargs: _WFResult(),
    )

    result = await asset_trainer.train_from_card(
        {
            "name": "Momentum Card",
            "filters": ["fast_fingers"],
            "params": {
                "signal_rules": [],
                "signal_logic": "AND",
            },
            "signal_mode": "algo_ai",
            "setup_family": "pullback_continuation",
            "strategy_spec": {},
        },
        symbol="XAUUSD",
        days=2,
        max_generations=1,
    )

    assert result["success"] is True
    assert captured["baseline_setup_family"] == "momentum_expansion"
    assert captured["version_params_setup_family"] == "momentum_expansion"
