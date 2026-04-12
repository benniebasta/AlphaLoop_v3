from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.backtester import asset_trainer
from alphaloop.backtester.params import BacktestParams
from alphaloop.trading.strategy_loader import (
    active_strategy_binding_keys,
    build_algorithmic_params,
    build_active_strategy_config,
    build_active_strategy_payload,
    bind_active_strategy_symbol,
    build_runtime_strategy_context,
    build_strategy_reference,
    build_strategy_resolution_input,
    build_strategy_version_tag,
    canonicalize_strategy_record,
    find_strategy_record,
    find_active_strategy_binding_for_version,
    load_active_strategy,
    load_active_strategy_bindings,
    load_active_strategy_payload,
    save_strategy_record,
    load_strategy_json,
    load_strategy_record,
    migrate_legacy_strategy_spec_v1,
    normalize_signal_mode,
    normalize_strategy_summary,
    normalize_strategy_signal_logic,
    normalize_strategy_signal_rules,
    resolve_algorithmic_setup_tag,
    resolve_signal_instruction,
    resolve_strategy_signal_logic,
    resolve_strategy_signal_rules,
    resolve_strategy_spec_version,
    resolve_strategy_version,
    resolve_strategy_source,
    resolve_strategy_setup_family,
    resolve_strategy_signal_mode,
    resolve_validator_instruction,
    resolve_strategy_version_string,
    serialize_strategy_spec,
    sync_active_strategy_bindings,
    store_active_strategy_bindings,
)


def test_normalize_signal_mode_accepts_canonical_values():
    assert normalize_signal_mode("algo_only") == "algo_only"
    assert normalize_signal_mode("algo_ai") == "algo_ai"
    assert normalize_signal_mode("ai_signal") == "ai_signal"


def test_normalize_signal_mode_defaults_unknown_to_ai_signal():
    assert normalize_signal_mode(None) == "ai_signal"
    assert normalize_signal_mode("legacy_mode") == "ai_signal"


def test_normalize_strategy_signal_rules_defaults_explicit_none_to_ema():
    assert normalize_strategy_signal_rules(None, default_to_ema=True) == [
        {"source": "ema_crossover"}
    ]


def test_normalize_strategy_signal_rules_fails_closed_on_malformed_shapes():
    assert normalize_strategy_signal_rules("ema_crossover") == []


def test_normalize_strategy_signal_logic_defaults_invalid_values_to_and():
    assert normalize_strategy_signal_logic(None) == "AND"
    assert normalize_strategy_signal_logic("weird") == "AND"
    assert normalize_strategy_signal_logic("or") == "OR"


def test_resolve_strategy_signal_rules_prefers_strategy_spec_entry_model_sources():
    strategy = {
        "params": {"signal_rules": [{"source": "ema_crossover"}]},
        "strategy_spec": {
            "entry_model": {
                "signal_rule_sources": ["macd_crossover", "rsi_reversal"],
            }
        },
    }

    assert resolve_strategy_signal_rules(strategy) == [
        {"source": "macd_crossover"},
        {"source": "rsi_reversal"},
    ]


def test_resolve_strategy_signal_rules_preserves_explicit_empty_list():
    strategy = {
        "params": {"signal_rules": [], "signal_logic": "OR"},
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "algo_ai",
            "setup_family": "trend_continuation",
        },
    }

    assert resolve_strategy_signal_rules(strategy, default_to_ema=True) == []


def test_resolve_strategy_signal_logic_prefers_strategy_spec_entry_model():
    strategy = {
        "params": {"signal_logic": "AND"},
        "strategy_spec": {
            "entry_model": {
                "signal_logic": "or",
            }
        },
    }

    assert resolve_strategy_signal_logic(strategy) == "OR"


def test_build_algorithmic_params_prefers_strategy_spec_entry_model():
    params = build_algorithmic_params(
        {
            "params": {
                "risk_pct": 0.01,
                "signal_rules": [{"source": "ema_crossover"}],
                "signal_logic": "AND",
            },
            "strategy_spec": {
                "entry_model": {
                    "signal_rule_sources": ["macd_crossover"],
                    "signal_logic": "OR",
                }
            },
        }
    )

    assert params["risk_pct"] == 0.01
    assert params["signal_rules"] == [{"source": "macd_crossover"}]
    assert params["signal_logic"] == "OR"


def test_build_strategy_resolution_input_preserves_explicit_none_signal_rules():
    base = BacktestParams.model_construct(
        signal_mode="algo_ai",
        setup_family="",
        strategy_spec={},
        tools={},
        source="legacy",
        signal_rules=None,
        signal_logic="AND",
    )

    payload = build_strategy_resolution_input(base)

    assert "signal_rules" in payload["params"]
    assert payload["params"]["signal_rules"] is None


def test_build_strategy_resolution_input_canonicalizes_explicit_strategy_spec():
    payload = build_strategy_resolution_input(
        {
            "signal_mode": "algo_only",
            "source": "backtest_runner",
            "strategy_spec": {
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": None,
            },
        }
    )

    assert payload["strategy_spec"]["spec_version"] == "v1"
    assert payload["strategy_spec"]["signal_mode"] == "ai_signal"
    assert payload["strategy_spec"]["setup_family"] == "discretionary_ai"
    assert payload["strategy_spec"]["metadata"]["source"] == "backtest_runner"


def test_build_strategy_resolution_input_preserves_canonical_identity_fields():
    payload = build_strategy_resolution_input(
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "status": "candidate",
            "signal_mode": "algo_only",
            "source": "",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "metadata": {
                    "source": "ui_ai_signal_card",
                    "version": 11,
                },
            },
        }
    )

    assert payload["version"] == 11
    assert payload["source"] == "ui_ai_signal_card"
    assert payload["spec_version"] == "v1"
    assert payload["strategy_spec"]["metadata"]["version"] == 11
    assert payload["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_active_strategy_binding_keys_orders_instance_before_symbol():
    keys = active_strategy_binding_keys(
        "XAUUSD",
        instance_id="bot-1",
        instance_ids=["bot-2", "bot-3"],
    )

    assert keys == [
        "active_strategy_bot-1",
        "active_strategy_bot-2",
        "active_strategy_bot-3",
        "active_strategy_XAUUSD",
    ]


def test_normalize_strategy_summary_prefers_alias_metrics():
    summary = normalize_strategy_summary(
        {
            "summary": {
                "sharpe_ratio": 1.1,
                "total_pnl_usd": 210.0,
                "max_drawdown_pct": -5.5,
            }
        }
    )

    assert summary["sharpe"] == 1.1
    assert summary["total_pnl"] == 210.0
    assert summary["max_dd_pct"] == -5.5


def test_canonicalize_strategy_record_prefers_spec_first_identity_fields():
    record = canonicalize_strategy_record(
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "spec_version": "legacy-v0",
            "signal_mode": "algo_only",
            "source": "",
            "signal_instruction": "legacy signal",
            "validator_instruction": "legacy validator",
            "ai_models": {"signal": "stale-signal"},
            "summary": {"sharpe_ratio": 1.5},
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {
                    "signal_instruction": "spec signal",
                    "validator_instruction": "spec validator",
                },
                "ai_models": {"signal": "spec-signal"},
                "metadata": {"source": "ui_ai_signal_card", "version": 9},
            },
        }
    )

    assert record["version"] == 9
    assert record["spec_version"] == "v1"
    assert record["signal_mode"] == "ai_signal"
    assert record["source"] == "ui_ai_signal_card"
    assert record["signal_instruction"] == "spec signal"
    assert record["validator_instruction"] == "spec validator"
    assert record["ai_models"] == {"signal": "spec-signal"}
    assert record["summary"]["sharpe"] == 1.5
    assert record["strategy_spec"]["metadata"]["version"] == 9


def test_load_strategy_record_prefers_strategy_spec_metadata_version(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v7.json"
    path.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": "legacy",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card", "version": 7},
        },
    }))

    record = load_strategy_record(path)

    assert record is not None
    assert record["version"] == 7
    assert record["source"] == "ui_ai_signal_card"
    assert record["_path"] == str(path)


def test_save_strategy_record_canonicalizes_before_writing(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v7.json"

    record = save_strategy_record(
        path,
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "spec_version": "legacy-v0",
            "signal_mode": "algo_only",
            "setup_family": "pullback_continuation",
            "source": "",
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
                "metadata": {
                    "version": 7,
                    "source": "ui_ai_signal_card",
                },
            },
        },
    )

    saved = json.loads(path.read_text())

    assert record["_path"] == str(path)
    assert saved["version"] == 7
    assert saved["spec_version"] == "v1"
    assert saved["signal_mode"] == "ai_signal"
    assert saved["setup_family"] == "discretionary_ai"
    assert saved["source"] == "ui_ai_signal_card"
    assert saved["signal_instruction"] == "spec signal"
    assert saved["validator_instruction"] == "spec validator"


def test_load_strategy_json_prefers_strategy_spec_metadata_version():
    record = load_strategy_json(json.dumps({
        "symbol": "XAUUSD",
        "version": "legacy",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card", "version": 7},
        },
    }))

    assert record is not None
    assert record["version"] == 7
    assert record["source"] == "ui_ai_signal_card"


def test_find_strategy_record_matches_canonical_metadata_version(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v7.json"
    path.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": "legacy",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card", "version": 7},
        },
    }))

    record = find_strategy_record("XAUUSD", 7, Path(tmp_path))

    assert record is not None
    assert record["version"] == 7
    assert record["source"] == "ui_ai_signal_card"


def test_build_strategy_reference_prefers_spec_first_version():
    ref = build_strategy_reference(
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "strategy_spec": {
                "metadata": {"version": 9},
            },
        }
    )

    assert ref == {
        "symbol": "XAUUSD",
        "strategy_id": "XAUUSD.v9",
        "strategy_version": "9",
    }


def test_runtime_strategy_context_json_prefers_spec_first_runtime_contract():
    signature = json.dumps(
        build_runtime_strategy_context(
            {
                "symbol": "XAUUSD",
                "version": "legacy",
                "signal_mode": "algo_only",
                "strategy_spec": {
                    "spec_version": "v1",
                    "signal_mode": "ai_signal",
                    "setup_family": "discretionary_ai",
                    "prompt_bundle": {},
                    "metadata": {"version": 13},
                },
            }
        ),
        sort_keys=True,
        default=str,
    )

    assert '"version": 13' in signature
    assert '"signal_mode": "ai_signal"' in signature


def test_active_strategy_payload_serializes_to_canonical_json():
    payload = json.loads(json.dumps(build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "signal_mode": "algo_only",
            "summary": {"sharpe_ratio": 1.1},
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
                "metadata": {"version": 14, "source": "ui_ai_signal_card"},
            },
        }
    )))

    assert payload["version"] == 14
    assert payload["signal_mode"] == "ai_signal"
    assert payload["summary"]["sharpe"] == 1.1


@pytest.mark.asyncio
async def test_store_active_strategy_bindings_writes_requested_keys_only():
    settings = SimpleNamespace(set=AsyncMock())

    payload = {
        "symbol": "OLD",
        "version": "legacy",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"version": 14},
        },
    }

    strategy_json = await store_active_strategy_bindings(
        settings,
        payload,
        symbol="XAUUSD",
        instance_id="bot-1",
        write_symbol_key=False,
        write_instance_key=True,
    )

    settings.set.assert_awaited_once_with("active_strategy_bot-1", strategy_json)
    persisted = json.loads(strategy_json)
    assert persisted["version"] == 14
    assert persisted["signal_mode"] == "ai_signal"


def test_bind_active_strategy_symbol_keeps_canonical_fields():
    payload = bind_active_strategy_symbol(
        {
            "symbol": "OLD",
            "version": "legacy",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
                "metadata": {"version": 7},
            },
        },
        "XAUUSD",
    )

    assert payload["symbol"] == "XAUUSD"
    assert payload["version"] == 7
    assert payload["signal_mode"] == "ai_signal"


def test_resolve_strategy_version_string_uses_spec_first_version():
    version = resolve_strategy_version_string(
        {
            "version": "legacy",
            "strategy_spec": {
                "metadata": {"version": 12},
            },
        }
    )

    assert version == "12"


def test_build_strategy_version_tag_uses_spec_first_version():
    version_tag = build_strategy_version_tag(
        {
            "version": "legacy",
            "strategy_spec": {
                "metadata": {"version": 12},
            },
        }
    )

    assert version_tag == "v12"


def test_create_strategy_version_defaults_to_algo_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams()
    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
    )
    assert version["signal_mode"] == "algo_ai"
    assert version["spec_version"] == "v1"
    assert version["strategy_spec"]["spec_version"] == "v1"
    assert version["strategy_spec"]["setup_family"] == "trend_continuation"


def test_build_active_strategy_payload_normalizes_summary_aliases():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 1,
            "summary": {
                "sharpe_ratio": 1.6,
                "total_pnl_usd": 333.0,
                "max_drawdown_pct": -9.0,
            },
        }
    )

    assert payload["summary"]["sharpe"] == 1.6
    assert payload["summary"]["total_pnl"] == 333.0
    assert payload["summary"]["max_dd_pct"] == -9.0


def test_create_strategy_version_allows_ai_signal_override(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams()
    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
        signal_mode="ai_signal",
    )
    assert version["signal_mode"] == "ai_signal"
    assert version["spec_version"] == "v1"
    assert version["strategy_spec"]["signal_mode"] == "ai_signal"
    assert version["strategy_spec"]["setup_family"] == "discretionary_ai"
    assert version["params"]["signal_rules"] == []
    assert version["strategy_spec"]["entry_model"]["signal_rule_sources"] == []


def test_create_strategy_version_preserves_explicit_empty_signal_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(signal_rules=[], signal_logic="OR")

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
    )

    assert version["params"]["signal_rules"] == []
    assert version["params"]["signal_logic"] == "OR"


def test_create_strategy_version_preserves_none_signal_rules_as_default_ema(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams.model_construct(
        signal_rules=None,
        signal_logic="AND",
        signal_mode="algo_ai",
        setup_family="",
        strategy_spec={},
        tools={},
        source="legacy",
        ema_fast=21,
        ema_slow=55,
        sl_atr_mult=2.0,
        tp1_rr=2.0,
        tp2_rr=4.0,
        rsi_period=14,
        rsi_ob=70.0,
        rsi_os=30.0,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        bb_period=20,
        bb_std_dev=2.0,
        adx_period=14,
        adx_min_threshold=20.0,
        volume_ma_period=20,
        risk_pct=0.01,
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
    )

    assert version["params"]["signal_rules"] == [{"source": "ema_crossover"}]
    assert version["strategy_spec"]["setup_family"] == "trend_continuation"


def test_create_strategy_version_normalizes_summary_metric_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=BacktestParams(),
        metrics={
            "total_trades": 12,
            "win_rate": 0.5,
            "sharpe_ratio": 1.25,
            "max_dd_pct": -6.4,
            "total_pnl_usd": 321.5,
        },
        tools=[],
    )

    assert version["summary"]["sharpe"] == 1.25
    assert version["summary"]["max_dd_pct"] == -6.4
    assert version["summary"]["total_pnl"] == 321.5


def test_create_strategy_version_prefers_params_tools_over_stale_tools_arg(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        tools={"fast_fingers": True, "ema200_filter": True},
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "algo_ai",
            "setup_family": "momentum_expansion",
        },
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=["bos_guard"],
    )

    assert version["tools"]["fast_fingers"] is True
    assert version["tools"]["ema200_filter"] is True
    assert version["tools"]["bos_guard"] is False
    assert version["validation"]["check_ema200_trend"] is True
    assert version["validation"]["check_bos"] is False


def test_create_strategy_version_preserves_typed_strategy_spec_from_params(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        signal_mode="ai_signal",
        setup_family="momentum_expansion",
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "momentum_expansion",
            "direction_model": "ai_hypothesis",
            "enabled_preconditions": ["session_filter"],
            "entry_model": {"type": "prompt_defined"},
            "invalidation_model": {"type": "structural_plus_atr"},
            "exit_policy": {"tp1_rr": 1.7},
            "risk_policy": {"risk_pct": 0.01},
            "prompt_bundle": {
                "signal_instruction": "spec signal prompt",
                "validator_instruction": "spec validator prompt",
            },
            "metadata": {"source": "train_from_card"},
        },
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=["session_filter"],
    )

    assert version["signal_mode"] == "ai_signal"
    assert version["signal_instruction"] == "spec signal prompt"
    assert version["validator_instruction"] == "spec validator prompt"
    assert version["strategy_spec"]["setup_family"] == "momentum_expansion"
    assert version["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "spec signal prompt"


def test_create_strategy_version_ignores_stale_explicit_signal_mode_when_params_spec_is_authoritative(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        signal_mode="ai_signal",
        setup_family="discretionary_ai",
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "spec signal prompt",
                "validator_instruction": "spec validator prompt",
            },
        },
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
        signal_mode="algo_only",
    )

    assert version["signal_mode"] == "ai_signal"
    assert version["strategy_spec"]["signal_mode"] == "ai_signal"


def test_create_strategy_version_prefers_params_strategy_spec_source_over_stale_explicit_source(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        signal_mode="ai_signal",
        setup_family="discretionary_ai",
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "metadata": {"source": "ui_ai_signal_card"},
        },
        source="legacy_flat_source",
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
        source="asset_trainer",
    )

    assert version["source"] == "ui_ai_signal_card"
    assert version["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_create_strategy_version_prefers_params_strategy_spec_ai_models_over_stale_explicit_ai_models(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        signal_mode="ai_signal",
        setup_family="discretionary_ai",
        strategy_spec={
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "ai_models": {"signal": "spec-signal", "validator": "spec-validator"},
        },
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        tools=[],
        metrics={},
        ai_models={"signal": "stale-signal"},
    )

    assert version["ai_models"] == {
        "signal": "spec-signal",
        "validator": "spec-validator",
    }
    assert version["strategy_spec"]["ai_models"] == {
        "signal": "spec-signal",
        "validator": "spec-validator",
    }


def test_create_strategy_version_ignores_stale_flat_setup_family_when_signal_mode_implies_ai(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        signal_mode="ai_signal",
        setup_family="pullback_continuation",
        strategy_spec={},
        source="ui_ai_signal_card",
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=[],
    )

    assert version["signal_mode"] == "ai_signal"
    assert version["strategy_spec"]["setup_family"] == "discretionary_ai"


def test_create_strategy_version_infers_family_from_enabled_tools_when_flat_family_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    params = BacktestParams(
        signal_mode="algo_ai",
        setup_family="pullback_continuation",
        strategy_spec={},
        signal_rules=[],
    )

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=params,
        metrics={},
        tools=["fast_fingers"],
    )

    assert version["signal_mode"] == "algo_ai"
    assert version["strategy_spec"]["setup_family"] == "momentum_expansion"


def test_create_strategy_version_retries_when_reserved_version_already_exists(tmp_path, monkeypatch):
    fixed_name = "test-hawk-TESTUSD_algo_ai"
    monkeypatch.setattr(asset_trainer, "STRATEGY_VERSIONS_DIR", Path(tmp_path))
    (Path(tmp_path) / f"{fixed_name}_v1.json").write_text("{}")

    versions = iter([1, 2])
    monkeypatch.setattr(asset_trainer, "_next_version", lambda name: next(versions))

    version = asset_trainer.create_strategy_version(
        symbol="TESTUSD",
        params=BacktestParams(),
        metrics={},
        tools=[],
        name=fixed_name,
    )

    assert version["_version"] == 2
    assert Path(version["_path"]).name == f"{fixed_name}_v2.json"
    assert not (Path(tmp_path) / f"{fixed_name}_v2.lock").exists()


@pytest.mark.asyncio
async def test_load_active_strategy_defaults_missing_spec_version_to_v1():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 12,
                    "status": "live",
                    "signal_mode": "algo_ai",
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is not None
    assert config.spec_version == "v1"
    assert config.signal_mode == "algo_ai"
    assert config.strategy_spec.spec_version == "v1"
    assert config.strategy_spec.signal_mode == "algo_ai"
    assert config.strategy_spec.setup_family == "pullback_continuation"


@pytest.mark.asyncio
async def test_load_active_strategy_payload_prefers_instance_binding():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": "legacy",
                    "signal_mode": "algo_only",
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "ai_signal",
                        "setup_family": "discretionary_ai",
                        "metadata": {"version": 17},
                    },
                }),
                None,
            ]
        )
    )

    payload = await load_active_strategy_payload(settings_service, "XAUUSD", "bot-1")

    assert payload is not None
    assert payload["version"] == 17
    assert payload["signal_mode"] == "ai_signal"
    assert settings_service.get.await_args_list[0].args[0] == "active_strategy_bot-1"


@pytest.mark.asyncio
async def test_load_active_strategy_bindings_returns_canonical_payloads_in_binding_order():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": "legacy",
                    "signal_mode": "algo_only",
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "ai_signal",
                        "setup_family": "discretionary_ai",
                        "metadata": {"version": 17},
                    },
                }),
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 3,
                    "signal_mode": "algo_only",
                }),
            ]
        )
    )

    bindings = await load_active_strategy_bindings(
        settings_service,
        "XAUUSD",
        instance_ids=["bot-2"],
        include_symbol=True,
    )

    assert [key for key, _ in bindings] == [
        "active_strategy_bot-2",
        "active_strategy_XAUUSD",
    ]
    assert bindings[0][1]["version"] == 17
    assert bindings[0][1]["signal_mode"] == "ai_signal"
    assert bindings[1][1]["version"] == 3
    assert bindings[1][1]["signal_mode"] == "algo_only"


@pytest.mark.asyncio
async def test_sync_active_strategy_bindings_updates_only_matching_symbol_and_version():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 7,
                    "signal_mode": "algo_only",
                }),
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 8,
                    "signal_mode": "algo_only",
                }),
                json.dumps({
                    "symbol": "EURUSD",
                    "version": 7,
                    "signal_mode": "algo_only",
                }),
            ]
        ),
        set=AsyncMock(),
    )

    updated = await sync_active_strategy_bindings(
        settings_service,
        "XAUUSD",
        {
            "symbol": "XAUUSD",
            "version": 7,
            "signal_mode": "ai_signal",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        },
        instance_ids=["bot-1", "bot-2"],
        include_symbol=True,
    )

    assert updated == ["active_strategy_bot-1"]
    assert settings_service.set.await_count == 1
    assert settings_service.set.await_args.args[0] == "active_strategy_bot-1"
    payload = json.loads(settings_service.set.await_args.args[1])
    assert payload["version"] == 7
    assert payload["signal_mode"] == "ai_signal"


@pytest.mark.asyncio
async def test_find_active_strategy_binding_for_version_prefers_canonical_version_match():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": "legacy",
                    "signal_mode": "algo_only",
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "ai_signal",
                        "setup_family": "discretionary_ai",
                        "metadata": {"version": 18},
                    },
                }),
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 7,
                    "signal_mode": "algo_only",
                }),
            ]
        )
    )

    binding = await find_active_strategy_binding_for_version(
        settings_service,
        "XAUUSD",
        18,
        instance_ids=["inst-1"],
        include_symbol=True,
    )

    assert binding is not None
    key, payload = binding
    assert key == "active_strategy_inst-1"
    assert payload["version"] == 18
    assert payload["signal_mode"] == "ai_signal"


@pytest.mark.asyncio
async def test_load_active_strategy_migrates_legacy_fields_into_strategy_spec():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 21,
                    "status": "candidate",
                    "signal_mode": "ai_signal",
                    "source": "ui_ai_signal_card",
                    "signal_instruction": "Find only clean pullbacks",
                    "validator_instruction": "Reject weak trades",
                    "params": {"risk_pct": 0.01, "tp1_rr": 1.5, "tp2_rr": 3.0},
                    "tools": {"session_filter": True, "news_filter": True},
                    "validation": {"min_confidence": 0.7, "min_rr": 1.1},
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is not None
    assert config.strategy_spec.setup_family == "discretionary_ai"
    assert config.strategy_spec.direction_model == "ai_hypothesis"
    assert config.strategy_spec.enabled_preconditions == ["session_filter", "news_filter"]
    assert config.strategy_spec.prompt_bundle["signal_instruction"] == "Find only clean pullbacks"
    assert config.strategy_spec.risk_policy["min_confidence"] == 0.7


@pytest.mark.asyncio
async def test_load_active_strategy_rejects_unknown_spec_version():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 13,
                    "spec_version": "v2",
                    "status": "live",
                    "signal_mode": "algo_ai",
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is None


@pytest.mark.asyncio
async def test_load_active_strategy_prefers_strategy_spec_version_over_stale_top_level():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 14,
                    "spec_version": "legacy-v0",
                    "status": "live",
                    "signal_mode": "algo_only",
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "ai_signal",
                        "setup_family": "discretionary_ai",
                    },
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is not None
    assert config.spec_version == "v1"
    assert config.strategy_spec.spec_version == "v1"


@pytest.mark.asyncio
async def test_load_active_strategy_prefers_strategy_spec_metadata_version():
    settings = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps(
                    {
                        "symbol": "XAUUSD",
                        "version": "legacy-v0",
                        "status": "live",
                        "signal_mode": "algo_only",
                        "strategy_spec": {
                            "spec_version": "v1",
                            "signal_mode": "algo_only",
                            "setup_family": "pullback_continuation",
                            "metadata": {"version": 13},
                        },
                    }
                ),
            ]
        )
    )

    config = await load_active_strategy(settings, "XAUUSD")

    assert config is not None
    assert config.version == 13
    assert config.strategy_spec.metadata["version"] == 13


@pytest.mark.asyncio
async def test_load_active_strategy_prefers_prompt_bundle_over_legacy_flat_fields():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 22,
                    "status": "live",
                    "signal_mode": "ai_signal",
                    "signal_instruction": "",
                    "validator_instruction": "",
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "ai_signal",
                        "setup_family": "discretionary_ai",
                        "prompt_bundle": {
                            "signal_instruction": "Spec signal prompt",
                            "validator_instruction": "Spec validator prompt",
                        },
                    },
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is not None
    assert config.signal_instruction == "Spec signal prompt"
    assert config.validator_instruction == "Spec validator prompt"


@pytest.mark.asyncio
async def test_load_active_strategy_builds_spec_first_algorithmic_params_from_entry_model():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 23,
                    "status": "live",
                    "signal_mode": "algo_ai",
                    "params": {
                        "risk_pct": 0.01,
                        "signal_rules": [{"source": "ema_crossover"}],
                        "signal_logic": "AND",
                    },
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "algo_ai",
                        "setup_family": "momentum_expansion",
                        "entry_model": {
                            "signal_rule_sources": ["macd_crossover"],
                            "signal_logic": "OR",
                        },
                    },
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is not None
    assert config.params["risk_pct"] == 0.01
    assert config.params["signal_rules"] == [{"source": "macd_crossover"}]
    assert config.params["signal_logic"] == "OR"


@pytest.mark.asyncio
async def test_load_active_strategy_normalizes_list_style_tools():
    settings_service = SimpleNamespace(
        get=AsyncMock(
            side_effect=[
                json.dumps({
                    "symbol": "XAUUSD",
                    "version": 24,
                    "status": "live",
                    "signal_mode": "algo_ai",
                    "tools": ["fast_fingers"],
                })
            ]
        )
    )

    config = await load_active_strategy(settings_service, "XAUUSD")

    assert config is not None
    assert config.tools == {"fast_fingers": True}


def test_serialize_strategy_spec_preserves_prompt_bundle_from_object():
    strategy = SimpleNamespace(
        symbol="XAUUSD",
        version=4,
        signal_mode="ai_signal",
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={},
        ai_models={},
        strategy_spec=SimpleNamespace(
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=["session_filter"],
            entry_model={"type": "prompt_defined"},
            invalidation_model={"type": "structural_plus_atr"},
            exit_policy={"tp1_rr": 1.5},
            risk_policy={"risk_pct": 0.01},
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            metadata={"source": "test"},
        ),
    )

    spec = serialize_strategy_spec(strategy)

    assert spec["prompt_bundle"]["signal_instruction"] == "spec signal"
    assert spec["prompt_bundle"]["validator_instruction"] == "spec validator"


def test_serialize_strategy_spec_preserves_object_fields_even_without_prompt_bundle():
    strategy = SimpleNamespace(
        symbol="XAUUSD",
        version=5,
        signal_mode="algo_only",
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={},
        ai_models={},
        strategy_spec=SimpleNamespace(
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=["session_filter"],
            entry_model={"type": "prompt_defined"},
            invalidation_model={"type": "structural_plus_atr"},
            exit_policy={"tp1_rr": 1.5},
            risk_policy={"risk_pct": 0.01},
            prompt_bundle=None,
            metadata={"source": "test"},
        ),
    )

    spec = serialize_strategy_spec(strategy)

    assert spec["signal_mode"] == "ai_signal"
    assert spec["setup_family"] == "discretionary_ai"
    assert spec["direction_model"] == "ai_hypothesis"
    assert spec["enabled_preconditions"] == ["session_filter"]
    assert spec["prompt_bundle"]["signal_instruction"] == "legacy signal"
    assert spec["prompt_bundle"]["validator_instruction"] == "legacy validator"


def test_serialize_strategy_spec_prefers_canonical_object_identity_fields():
    strategy = SimpleNamespace(
        symbol="XAUUSD",
        version="legacy",
        source="",
        spec_version="legacy-v0",
        signal_mode="algo_only",
        signal_instruction="legacy signal",
        validator_instruction="legacy validator",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={},
        ai_models={"validator": "stale-validator"},
        strategy_spec=SimpleNamespace(
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=["session_filter"],
            entry_model={"type": "prompt_defined"},
            invalidation_model={"type": "structural_plus_atr"},
            exit_policy={"tp1_rr": 1.5},
            risk_policy={"risk_pct": 0.01},
            prompt_bundle=None,
            ai_models={"validator": "spec-validator"},
            metadata={"source": "ui_ai_signal_card", "version": 9},
        ),
    )

    spec = serialize_strategy_spec(strategy)

    assert spec["spec_version"] == "v1"
    assert spec["signal_mode"] == "ai_signal"
    assert spec["ai_models"] == {"validator": "spec-validator"}
    assert spec["metadata"]["source"] == "ui_ai_signal_card"
    assert spec["metadata"]["version"] == 9


def test_prompt_resolution_prefers_strategy_spec_prompt_bundle():
    strategy = {
        "signal_instruction": "legacy signal",
        "validator_instruction": "legacy validator",
        "strategy_spec": {
            "prompt_bundle": {
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            }
        },
    }

    assert resolve_signal_instruction(strategy) == "spec signal"
    assert resolve_validator_instruction(strategy) == "spec validator"


def test_resolve_strategy_signal_mode_prefers_spec_and_normalizes():
    strategy = {
        "signal_mode": "algo_only",
        "strategy_spec": {
            "signal_mode": "ALGO_AI",
        },
    }

    assert resolve_strategy_signal_mode(strategy) == "algo_ai"


def test_resolve_strategy_signal_mode_ignores_empty_strategy_spec_dict():
    strategy = {
        "signal_mode": "algo_ai",
        "strategy_spec": {},
    }

    assert resolve_strategy_signal_mode(strategy) == "algo_ai"


def test_resolve_strategy_spec_version_prefers_strategy_spec():
    strategy = {
        "spec_version": "legacy-v0",
        "strategy_spec": {
            "spec_version": "v1",
        },
    }

    assert resolve_strategy_spec_version(strategy) == "v1"


def test_resolve_strategy_source_prefers_strategy_spec_metadata():
    strategy = {
        "source": "legacy_flat_source",
        "strategy_spec": {
            "metadata": {
                "source": "ui_ai_signal_card",
            }
        },
    }

    assert resolve_strategy_source(strategy) == "ui_ai_signal_card"


def test_resolve_strategy_setup_family_and_algorithmic_tag():
    strategy = {
        "signal_mode": "algo_ai",
        "strategy_spec": {
            "setup_family": "momentum_expansion",
        },
    }

    assert resolve_strategy_setup_family(strategy) == "momentum_expansion"
    assert resolve_algorithmic_setup_tag(strategy) == "momentum"


def test_migrate_strategy_spec_prefers_spec_signal_mode_for_direction_default():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
            },
        }
    )

    assert spec.signal_mode == "ai_signal"
    assert spec.direction_model == "ai_hypothesis"


def test_migrate_strategy_spec_falls_back_to_flat_prompts_when_bundle_missing():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "symbol": "XAUUSD",
            "version": 9,
            "source": "ui_ai_signal_card",
            "signal_mode": "ai_signal",
            "signal_instruction": "flat signal prompt",
            "validator_instruction": "flat validator prompt",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        }
    )

    assert spec.prompt_bundle["signal_instruction"] == "flat signal prompt"
    assert spec.prompt_bundle["validator_instruction"] == "flat validator prompt"
    assert spec.metadata["source"] == "ui_ai_signal_card"
    assert spec.metadata["symbol"] == "XAUUSD"
    assert spec.metadata["version"] == 9


def test_migrate_strategy_spec_backfills_entry_model_from_legacy_fields_when_explicit_spec_is_partial():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "params": {
                "signal_rules": [{"source": "macd_crossover"}],
                "signal_logic": "OR",
            },
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "momentum_expansion",
                "entry_model": {},
            },
        }
    )

    assert spec.entry_model["type"] == "rule_derived"
    assert spec.entry_model["signal_logic"] == "OR"
    assert spec.entry_model["signal_rule_sources"] == ["macd_crossover"]


def test_migrate_strategy_spec_infers_family_from_resolved_spec_signal_mode():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "source": "ui_ai_signal_card",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "",
            },
        }
    )

    assert spec.signal_mode == "ai_signal"
    assert spec.setup_family == "discretionary_ai"


def test_migrate_strategy_spec_infers_family_from_strategy_spec_metadata_source():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "source": "",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_only",
                "setup_family": "",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        }
    )

    assert spec.setup_family == "discretionary_ai"
    assert spec.metadata["source"] == "ui_ai_signal_card"


def test_migrate_strategy_spec_prefers_breakout_tools_over_generic_ema_rule():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "params": {"signal_rules": [{"source": "ema_crossover"}]},
            "tools": {"bos_guard": True},
        }
    )

    assert spec.setup_family == "breakout_retest"


def test_migrate_strategy_spec_prefers_momentum_tools_over_generic_ema_rule():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "params": {"signal_rules": [{"source": "ema_crossover"}]},
            "tools": {"fast_fingers": True},
        }
    )

    assert spec.setup_family == "momentum_expansion"


def test_migrate_strategy_spec_infers_family_from_flat_signal_rules_when_params_missing():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "signal_rules": [{"source": "ema_crossover"}],
        }
    )

    assert spec.setup_family == "trend_continuation"
    assert spec.entry_model["signal_logic"] == "AND"
    assert spec.entry_model["signal_rule_sources"] == ["ema_crossover"]


def test_migrate_strategy_spec_preserves_flat_signal_logic_when_params_missing():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "OR",
        }
    )

    assert spec.entry_model["signal_logic"] == "OR"


def test_migrate_strategy_spec_defaults_explicit_none_signal_rules_for_backward_compat():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "params": {
                "signal_rules": None,
                "signal_logic": None,
            },
        }
    )

    assert spec.setup_family == "trend_continuation"
    assert spec.entry_model["signal_logic"] == "AND"
    assert spec.entry_model["signal_rule_sources"] == ["ema_crossover"]


def test_migrate_strategy_spec_fails_closed_on_malformed_signal_rules():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "params": {
                "signal_rules": "ema_crossover",
                "signal_logic": "OR",
            },
        }
    )

    assert spec.setup_family == "pullback_continuation"
    assert spec.entry_model["signal_logic"] == "OR"
    assert spec.entry_model["signal_rule_sources"] == []


def test_migrate_strategy_spec_infers_family_from_list_style_tools():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "signal_mode": "algo_ai",
            "tools": ["bos_guard"],
            "signal_rules": [{"source": "ema_crossover"}],
        }
    )

    assert spec.setup_family == "breakout_retest"


def test_migrate_strategy_spec_ignores_stale_flat_setup_family_when_explicit_spec_family_missing():
    spec = migrate_legacy_strategy_spec_v1(
        {
            "setup_family": "pullback_continuation",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        }
    )

    assert spec.signal_mode == "ai_signal"
    assert spec.setup_family == "discretionary_ai"


def test_resolve_strategy_setup_family_ignores_stale_flat_family_when_explicit_spec_family_missing():
    family = resolve_strategy_setup_family(
        {
            "setup_family": "pullback_continuation",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        }
    )

    assert family == "discretionary_ai"


def test_build_runtime_strategy_context_is_explicit_and_spec_aware():
    strategy = SimpleNamespace(
        symbol="XAUUSD",
        version=7,
        status="live",
        spec_version="v1",
        signal_mode="ai_signal",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={"min_rr": 1.2},
        ai_models={"validator": "gpt-5.4-mini"},
        scoring_weights={"trend": 0.4},
        confidence_thresholds={"min_entry": 58.0},
        strategy_spec=SimpleNamespace(
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            ai_models={"validator": "spec-validator"},
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=["session_filter"],
            entry_model={"type": "prompt_defined"},
            invalidation_model={"type": "structural_plus_atr"},
            exit_policy={"tp1_rr": 1.5},
            risk_policy={"risk_pct": 0.01},
            metadata={"source": "test"},
        ),
    )

    payload = build_runtime_strategy_context(strategy)

    assert payload["symbol"] == "XAUUSD"
    assert payload["version"] == 7
    assert payload["setup_family"] == "discretionary_ai"
    assert payload["source"] == "test"
    assert payload["signal_instruction"] == "spec signal"
    assert payload["validator_instruction"] == "spec validator"
    assert payload["ai_models"] == {"validator": "spec-validator"}
    assert payload["scoring_weights"] == {"trend": 0.4}
    assert payload["confidence_thresholds"] == {"min_entry": 58.0}
    assert payload["strategy_spec"]["setup_family"] == "discretionary_ai"
    assert payload["strategy_spec"]["ai_models"] == {"validator": "spec-validator"}


@pytest.mark.asyncio
async def test_load_active_strategy_prefers_strategy_spec_ai_models():
    settings_service = AsyncMock()
    settings_service.get = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "symbol": "XAUUSD",
                    "version": 7,
                    "status": "live",
                    "ai_models": {"validator": "stale-validator"},
                    "strategy_spec": {
                        "spec_version": "v1",
                        "signal_mode": "ai_signal",
                        "setup_family": "discretionary_ai",
                        "ai_models": {"validator": "spec-validator"},
                    },
                }
            )
        ]
    )

    strategy = await load_active_strategy(settings_service, "XAUUSD", "bot-1")

    assert strategy is not None
    assert strategy.ai_models == {"validator": "spec-validator"}
    assert strategy.strategy_spec.ai_models == {"validator": "spec-validator"}


def test_build_active_strategy_payload_preserves_conviction_tuning_fields():
    strategy = SimpleNamespace(
        symbol="XAUUSD",
        version=8,
        status="live",
        spec_version="v1",
        signal_mode="ai_signal",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={"min_rr": 1.2},
        ai_models={"validator": "gpt-5.4-mini"},
        scoring_weights={"trend": 0.4, "momentum": 0.3},
        confidence_thresholds={"min_entry": 58.0, "strong_entry": 74.0},
        summary={"sharpe": 1.8},
        strategy_spec=SimpleNamespace(
            prompt_bundle={
                "signal_instruction": "spec signal",
                "validator_instruction": "spec validator",
            },
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            direction_model="ai_hypothesis",
            enabled_preconditions=["session_filter"],
            entry_model={"type": "prompt_defined"},
            invalidation_model={"type": "structural_plus_atr"},
            exit_policy={"tp1_rr": 1.5},
            risk_policy={"risk_pct": 0.01},
            metadata={"source": "test"},
        ),
    )

    payload = build_active_strategy_payload(strategy)

    assert payload["scoring_weights"] == {"trend": 0.4, "momentum": 0.3}
    assert payload["confidence_thresholds"] == {"min_entry": 58.0, "strong_entry": 74.0}
    assert payload["summary"]["sharpe"] == 1.8
    assert payload["summary"]["total_pnl"] == 0
    assert payload["summary"]["max_dd_pct"] == 0


def test_build_active_strategy_payload_prefers_strategy_spec_version():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 8,
            "status": "live",
            "spec_version": "legacy-v0",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
            },
        }
    )

    assert payload["spec_version"] == "v1"


def test_build_active_strategy_payload_prefers_strategy_spec_metadata_source():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 8,
            "status": "live",
            "source": "",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_only",
                "setup_family": "pullback_continuation",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        }
    )

    assert payload["source"] == "ui_ai_signal_card"


def test_build_active_strategy_config_prefers_canonical_strategy_contract():
    config = build_active_strategy_config(
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "status": "live",
            "signal_mode": "algo_only",
            "tools": ["fast_fingers"],
            "signal_instruction": "",
            "validator_instruction": "",
            "ai_models": {"validator": "stale-validator"},
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {
                    "signal_instruction": "Spec signal",
                    "validator_instruction": "Spec validator",
                },
                "ai_models": {"validator": "spec-validator"},
                "metadata": {"version": 14},
            },
        }
    )

    assert config.version == 14
    assert config.signal_mode == "ai_signal"
    assert config.signal_instruction == "Spec signal"
    assert config.validator_instruction == "Spec validator"
    assert config.tools == {"fast_fingers": True}
    assert config.ai_models == {"validator": "spec-validator"}


def test_build_runtime_strategy_context_prefers_spec_first_family_and_source():
    payload = build_runtime_strategy_context(
        {
            "symbol": "XAUUSD",
            "version": 9,
            "status": "live",
            "signal_mode": "algo_only",
            "setup_family": "pullback_continuation",
            "source": "legacy_flat_source",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        }
    )

    assert payload["signal_mode"] == "ai_signal"
    assert payload["setup_family"] == "discretionary_ai"
    assert payload["source"] == "ui_ai_signal_card"


def test_resolve_strategy_version_prefers_strategy_spec_metadata():
    strategy = {
        "version": "legacy-v0",
        "strategy_spec": {
            "metadata": {"version": 12},
        },
    }

    assert resolve_strategy_version(strategy) == 12


def test_build_runtime_strategy_context_prefers_strategy_spec_metadata_version():
    payload = build_runtime_strategy_context(
        {
            "symbol": "XAUUSD",
            "version": "legacy-v0",
            "status": "live",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_only",
                "setup_family": "pullback_continuation",
                "metadata": {"version": 14},
            },
        }
    )

    assert payload["version"] == 14


def test_build_runtime_strategy_context_normalizes_list_style_tools():
    strategy = SimpleNamespace(
        symbol="XAUUSD",
        version=7,
        status="candidate",
        signal_mode="algo_ai",
        params={"signal_rules": [{"source": "ema_crossover"}]},
        tools=["fast_fingers"],
        validation={},
        ai_models={},
        strategy_spec=SimpleNamespace(
            prompt_bundle={},
            spec_version="v1",
            signal_mode="algo_ai",
            setup_family="",
            direction_model="algorithmic_rules",
            enabled_preconditions=[],
            entry_model={},
            invalidation_model={},
            exit_policy={},
            risk_policy={},
            metadata={},
        ),
    )

    payload = build_runtime_strategy_context(strategy)

    assert payload["tools"] == {"fast_fingers": True}
    assert payload["setup_family"] == "momentum_expansion"


def test_build_runtime_strategy_context_prefers_strategy_spec_entry_model_in_params():
    payload = build_runtime_strategy_context(
        {
            "symbol": "XAUUSD",
            "version": 10,
            "status": "live",
            "params": {
                "risk_pct": 0.01,
                "signal_rules": [{"source": "ema_crossover"}],
                "signal_logic": "AND",
            },
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "momentum_expansion",
                "entry_model": {
                    "signal_rule_sources": ["macd_crossover"],
                    "signal_logic": "OR",
                },
            },
        }
    )

    assert payload["params"]["risk_pct"] == 0.01
    assert payload["params"]["signal_rules"] == [{"source": "macd_crossover"}]
    assert payload["params"]["signal_logic"] == "OR"


def test_build_runtime_strategy_context_defaults_invalid_version_to_zero():
    payload = build_runtime_strategy_context(
        {
            "symbol": "XAUUSD",
            "version": "legacy",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        }
    )

    assert payload["version"] == 0
