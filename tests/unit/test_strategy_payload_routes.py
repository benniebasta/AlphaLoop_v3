from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from alphaloop.db.models.instance import RunningInstance
from alphaloop.trading.strategy_loader import (
    build_active_strategy_payload,
    bind_active_strategy_symbol,
    load_strategy_json,
)
from alphaloop.webui.routes.bots import _bot_to_dict
import alphaloop.webui.routes.strategies as strategies_route
from alphaloop.webui.routes.strategies import (
    _effective_signal_mode,
    _normalized_summary,
    _save_version,
    _sync_strategy_spec_write_fields,
)


def test_active_strategy_payload_prefers_spec_prompt_bundle():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 11,
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
            },
        }
    )

    assert payload["signal_instruction"] == "spec signal"
    assert payload["validator_instruction"] == "spec validator"
    assert payload["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "spec signal"


def test_active_strategy_payload_prefers_spec_signal_mode():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 13,
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        }
    )

    assert payload["signal_mode"] == "ai_signal"


def test_load_active_strategy_record_prefers_spec_first_version_and_source():
    payload = load_strategy_json(json.dumps({
        "symbol": "XAUUSD",
        "version": "legacy",
        "signal_mode": "algo_only",
        "source": "",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card", "version": 7},
        },
    }))

    assert payload is not None
    assert payload["version"] == 7
    assert payload["signal_mode"] == "ai_signal"
    assert payload["source"] == "ui_ai_signal_card"


def test_active_strategy_payload_prefers_spec_version():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 14,
            "spec_version": "legacy-v0",
            "signal_mode": "ai_signal",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        }
    )

    assert payload["spec_version"] == "v1"


def test_effective_signal_mode_prefers_strategy_spec():
    assert _effective_signal_mode(
        {
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        }
    ) == "ai_signal"


def test_active_strategy_payload_preserves_conviction_tuning_fields():
    payload = build_active_strategy_payload(
        {
            "symbol": "XAUUSD",
            "version": 12,
            "signal_mode": "ai_signal",
            "scoring_weights": {"trend": 0.4},
            "confidence_thresholds": {"min_entry": 59.0},
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        }
    )

    assert payload["scoring_weights"] == {"trend": 0.4}
    assert payload["confidence_thresholds"] == {"min_entry": 59.0}


def test_normalized_summary_prefers_alias_fields():
    summary = _normalized_summary(
        {
            "summary": {
                "sharpe_ratio": 1.4,
                "total_pnl_usd": 250.0,
                "max_drawdown_pct": -6.5,
            }
        }
    )

    assert summary["sharpe"] == 1.4
    assert summary["total_pnl"] == 250.0
    assert summary["max_dd_pct"] == -6.5


def test_bot_to_dict_prefers_spec_signal_mode():
    bot = RunningInstance(
        id=1,
        symbol="XAUUSD",
        instance_id="bot-1",
        pid=1234,
        strategy_version="v1",
        started_at=datetime.now(timezone.utc),
    )
    payload = _bot_to_dict(
        bot,
        {
            "name": "Spec-first",
            "version": 8,
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

    assert payload["strategy"]["signal_mode"] == "ai_signal"


def test_bot_to_dict_falls_back_to_bound_strategy_version_when_record_is_blank():
    bot = RunningInstance(
        id=11,
        symbol="XAUUSD",
        instance_id="bot-11",
        pid=1111,
        strategy_version=None,
        started_at=datetime.now(timezone.utc),
    )
    payload = _bot_to_dict(
        bot,
        {
            "name": "Bound Version",
            "version": 8,
            "signal_mode": "algo_ai",
            "summary": {},
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "trend_continuation",
                "prompt_bundle": {},
            },
        },
    )

    assert payload["strategy_version"] == "v8"


def test_bot_to_dict_normalizes_tools_and_prefers_max_drawdown_pct_summary_key():
    bot = RunningInstance(
        id=2,
        symbol="XAUUSD",
        instance_id="bot-2",
        pid=4321,
        strategy_version="v2",
        started_at=datetime.now(timezone.utc),
    )
    payload = _bot_to_dict(
        bot,
        {
            "name": "Legacy Bound",
            "version": 9,
            "signal_mode": "algo_ai",
            "tools": ["fast_fingers"],
            "summary": {
                "win_rate": 0.51,
                "sharpe": 1.2,
                "max_drawdown_pct": -7.5,
                "total_pnl": 123.0,
            },
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "momentum_expansion",
                "prompt_bundle": {},
            },
        },
    )

    assert payload["strategy"]["tools"] == {"fast_fingers": True}
    assert payload["strategy"]["metrics"]["max_dd_pct"] == -7.5


def test_bot_to_dict_prefers_sharpe_ratio_and_total_pnl_usd_aliases():
    bot = RunningInstance(
        id=3,
        symbol="XAUUSD",
        instance_id="bot-3",
        pid=8765,
        strategy_version="v3",
        started_at=datetime.now(timezone.utc),
    )
    payload = _bot_to_dict(
        bot,
        {
            "name": "Alias Bound",
            "version": 10,
            "signal_mode": "algo_ai",
            "tools": {},
            "summary": {
                "win_rate": 0.48,
                "sharpe_ratio": 1.35,
                "max_dd_pct": -4.2,
                "total_pnl_usd": 456.0,
            },
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "trend_continuation",
                "prompt_bundle": {},
            },
        },
    )

    assert payload["strategy"]["metrics"]["sharpe"] == 1.35
    assert payload["strategy"]["metrics"]["total_pnl"] == 456.0


def test_bound_active_strategy_payload_prefers_spec_version():
    payload = bind_active_strategy_symbol(
        {
            "symbol": "XAUUSD",
            "version": 8,
            "spec_version": "legacy-v0",
            "status": "dry_run",
            "signal_mode": "algo_only",
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "prompt_bundle": {},
            },
        },
        "XAUUSD",
    )

    assert payload["spec_version"] == "v1"
    assert payload["strategy_spec"]["spec_version"] == "v1"


def test_bot_to_dict_prefers_canonical_bound_strategy_version():
    bot = RunningInstance(
        id=1,
        symbol="XAUUSD",
        instance_id="bot-1",
        pid=1234,
        strategy_version=None,
        started_at=datetime.now(timezone.utc),
    )
    bound = {
        "symbol": "XAUUSD",
        "version": "legacy",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"version": 9},
        },
    }

    payload = _bot_to_dict(bot, bound)

    assert payload["strategy_version"] == "v9"
    assert payload["strategy"]["version"] == 9


def test_bound_active_strategy_payload_normalizes_summary_aliases():
    payload = bind_active_strategy_symbol(
        {
            "symbol": "XAUUSD",
            "version": 9,
            "status": "dry_run",
            "signal_mode": "algo_ai",
            "summary": {
                "sharpe_ratio": 0.9,
                "total_pnl_usd": 88.0,
                "max_drawdown_pct": -3.0,
            },
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "trend_continuation",
                "prompt_bundle": {},
            },
        },
        "XAUUSD",
    )

    assert payload["summary"]["sharpe"] == 0.9
    assert payload["summary"]["total_pnl"] == 88.0
    assert payload["summary"]["max_dd_pct"] == -3.0


def test_load_version_prefers_spec_signal_mode(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v1.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 1,
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_version("XAUUSD", 1)

    assert loaded is not None
    assert loaded["signal_mode"] == "ai_signal"


def test_load_version_prefers_spec_prompt_bundle_fields(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v2.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 2,
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
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_version("XAUUSD", 2)

    assert loaded is not None
    assert loaded["signal_instruction"] == "spec signal"
    assert loaded["validator_instruction"] == "spec validator"


def test_load_version_prefers_spec_family_source_and_version(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v14.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 14,
        "spec_version": "legacy-v0",
        "setup_family": "pullback_continuation",
        "source": "",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_version("XAUUSD", 14)

    assert loaded is not None
    assert loaded["spec_version"] == "v1"
    assert loaded["setup_family"] == "discretionary_ai"
    assert loaded["source"] == "ui_ai_signal_card"


def test_load_version_normalizes_summary_metric_aliases(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v3.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 3,
        "signal_mode": "algo_ai",
        "summary": {
            "sharpe_ratio": 1.25,
            "total_pnl_usd": 321.0,
            "max_drawdown_pct": -8.0,
        },
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "algo_ai",
            "setup_family": "trend_continuation",
            "prompt_bundle": {},
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_version("XAUUSD", 3)

    assert loaded is not None
    assert loaded["summary"]["sharpe"] == 1.25
    assert loaded["summary"]["total_pnl"] == 321.0
    assert loaded["summary"]["max_dd_pct"] == -8.0


def test_load_version_prefers_spec_ai_models(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v4.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 4,
        "signal_mode": "ai_signal",
        "ai_models": {"signal": "stale-signal"},
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "ai_models": {"signal": "spec-signal", "validator": "spec-validator"},
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_version("XAUUSD", 4)

    assert loaded is not None
    assert loaded["ai_models"] == {
        "signal": "spec-signal",
        "validator": "spec-validator",
    }


def test_load_all_versions_prefers_spec_signal_mode(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v2.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 2,
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_all_versions()

    assert len(loaded) == 1
    assert loaded[0]["signal_mode"] == "ai_signal"


def test_load_all_versions_prefers_spec_prompt_bundle_fields(tmp_path, monkeypatch):
    strategy_file = Path(tmp_path) / "XAUUSD_v8.json"
    strategy_file.write_text(json.dumps({
        "symbol": "XAUUSD",
        "version": 8,
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
        },
    }))
    monkeypatch.setattr(strategies_route, "STRATEGY_VERSIONS_DIR", Path(tmp_path))

    loaded = strategies_route._load_all_versions()

    assert len(loaded) == 1
    assert loaded[0]["signal_instruction"] == "spec signal"
    assert loaded[0]["validator_instruction"] == "spec validator"


def test_sync_strategy_spec_write_fields_updates_prompt_bundle_and_mode():
    data = {
        "symbol": "XAUUSD",
        "version": 3,
        "source": "ui_ai_signal_card",
        "signal_mode": "algo_only",
        "signal_instruction": "new signal prompt",
        "validator_instruction": "new validator prompt",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {
                "signal_instruction": "old signal prompt",
                "validator_instruction": "old validator prompt",
            },
            "metadata": {"source": "legacy"},
        },
    }

    _sync_strategy_spec_write_fields(
        data,
        {"signal_mode", "signal_instruction", "validator_instruction", "source"},
    )

    assert data["strategy_spec"]["signal_mode"] == "algo_only"
    assert data["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "new signal prompt"
    assert data["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "new validator prompt"
    assert data["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_sync_strategy_spec_write_fields_updates_ai_models():
    data = {
        "symbol": "XAUUSD",
        "version": 3,
        "source": "ui_ai_signal_card",
        "signal_mode": "ai_signal",
        "signal_instruction": "signal prompt",
        "validator_instruction": "validator prompt",
        "ai_models": {"signal": "gpt-5.4-mini", "validator": "gpt-5.4"},
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "ai_models": {"signal": "stale-signal"},
            "metadata": {"source": "legacy"},
        },
    }

    _sync_strategy_spec_write_fields(data, {"ai_models"})

    assert data["strategy_spec"]["ai_models"] == {
        "signal": "gpt-5.4-mini",
        "validator": "gpt-5.4",
    }


def test_sync_strategy_spec_write_fields_preserves_effective_metadata_source_when_flat_source_blank():
    data = {
        "symbol": "XAUUSD",
        "version": 4,
        "source": "",
        "signal_mode": "ai_signal",
        "signal_instruction": "signal prompt",
        "validator_instruction": "validator prompt",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }

    _sync_strategy_spec_write_fields(data)

    assert data["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_sync_strategy_spec_write_fields_preserves_effective_metadata_source_when_blank_source_is_explicit():
    data = {
        "symbol": "XAUUSD",
        "version": 4,
        "source": "",
        "signal_mode": "ai_signal",
        "signal_instruction": "signal prompt",
        "validator_instruction": "validator prompt",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }

    _sync_strategy_spec_write_fields(data, {"source"})

    assert data["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"


def test_sync_strategy_spec_write_fields_prefers_spec_first_metadata_version():
    data = {
        "symbol": "XAUUSD",
        "version": "legacy",
        "source": "ui_ai_signal_card",
        "signal_mode": "ai_signal",
        "signal_instruction": "signal prompt",
        "validator_instruction": "validator prompt",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card", "version": 6},
        },
    }

    _sync_strategy_spec_write_fields(data)

    assert data["strategy_spec"]["metadata"]["version"] == 6


def test_sync_strategy_spec_write_fields_allows_explicit_prompt_clear():
    data = {
        "symbol": "XAUUSD",
        "version": 6,
        "source": "ui_ai_signal_card",
        "signal_mode": "ai_signal",
        "signal_instruction": "spec signal",
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
    }

    _sync_strategy_spec_write_fields(data, {"validator_instruction"})

    assert data["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "spec signal"
    assert data["strategy_spec"]["prompt_bundle"]["validator_instruction"] == ""


def test_save_version_preserves_spec_prompts_on_non_prompt_save(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v5.json"
    path.write_text("{}")
    data = {
        "_path": str(path),
        "symbol": "XAUUSD",
        "version": 5,
        "status": "dry_run",
        "source": "ui_ai_signal_card",
        "signal_mode": "algo_only",
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
    }

    _save_version(data)

    saved = json.loads(path.read_text())
    assert saved["signal_mode"] == "ai_signal"
    assert saved["signal_instruction"] == "spec signal"
    assert saved["validator_instruction"] == "spec validator"
    assert saved["strategy_spec"]["prompt_bundle"]["signal_instruction"] == "spec signal"
    assert saved["strategy_spec"]["prompt_bundle"]["validator_instruction"] == "spec validator"


def test_save_version_normalizes_summary_metric_aliases(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v6.json"
    path.write_text("{}")
    data = {
        "_path": str(path),
        "symbol": "XAUUSD",
        "version": 6,
        "status": "candidate",
        "source": "strategies",
        "signal_mode": "algo_ai",
        "summary": {
            "sharpe_ratio": 1.1,
            "total_pnl_usd": 222.0,
            "max_drawdown_pct": -4.5,
        },
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "algo_ai",
            "setup_family": "trend_continuation",
            "prompt_bundle": {},
            "metadata": {"source": "strategies"},
        },
    }

    _save_version(data)

    saved = json.loads(path.read_text())
    assert saved["summary"]["sharpe"] == 1.1
    assert saved["summary"]["total_pnl"] == 222.0
    assert saved["summary"]["max_dd_pct"] == -4.5


def test_save_version_preserves_spec_first_ai_models_on_non_model_save(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v7.json"
    path.write_text("{}")
    data = {
        "_path": str(path),
        "symbol": "XAUUSD",
        "version": 7,
        "status": "candidate",
        "source": "ui_ai_signal_card",
        "signal_mode": "ai_signal",
        "ai_models": {"signal": "stale-signal"},
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "ai_models": {
                "signal": "spec-signal",
                "validator": "spec-validator",
            },
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }

    _save_version(data)

    saved = json.loads(path.read_text())
    assert saved["ai_models"] == {
        "signal": "spec-signal",
        "validator": "spec-validator",
    }
    assert saved["strategy_spec"]["ai_models"] == {
        "signal": "spec-signal",
        "validator": "spec-validator",
    }


def test_save_version_preserves_spec_first_family_source_and_version(tmp_path):
    path = Path(tmp_path) / "XAUUSD_v15.json"
    path.write_text("{}")
    data = {
        "_path": str(path),
        "symbol": "XAUUSD",
        "version": 15,
        "status": "candidate",
        "spec_version": "legacy-v0",
        "setup_family": "pullback_continuation",
        "source": "",
        "signal_mode": "algo_only",
        "strategy_spec": {
            "spec_version": "v1",
            "signal_mode": "ai_signal",
            "setup_family": "discretionary_ai",
            "prompt_bundle": {},
            "metadata": {"source": "ui_ai_signal_card"},
        },
    }

    _save_version(data)

    saved = json.loads(path.read_text())
    assert saved["spec_version"] == "v1"
    assert saved["setup_family"] == "discretionary_ai"
    assert saved["source"] == "ui_ai_signal_card"
