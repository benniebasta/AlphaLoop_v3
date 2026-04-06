import json

from alphaloop.webui.routes.backtests import (
    _build_backtest_plan_payload,
    _extract_backtest_setup_family,
    _extract_backtest_signal_logic,
    _extract_backtest_signal_mode,
    _extract_backtest_signal_rules,
    _extract_backtest_strategy_spec,
    _extract_backtest_tools,
    _extract_backtest_source,
)


def test_extract_backtest_signal_mode_prefers_strategy_spec_before_normalization():
    plan = json.dumps(
        {
            "signal_mode": "algo_only",
            "strategy_spec": {
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
            },
        }
    )

    assert _extract_backtest_signal_mode(plan) == "algo_ai"


def test_build_backtest_plan_payload_infers_family_and_source_from_tools():
    payload = _build_backtest_plan_payload(
        signal_mode="algo_ai",
        signal_rules=[{"source": "ema_crossover"}],
        signal_logic="AND",
        signal_auto=False,
        tools=["fast_fingers"],
    )

    assert payload["signal_mode"] == "algo_ai"
    assert payload["setup_family"] == "momentum_expansion"
    assert payload["source"] == "backtest_runner"
    assert payload["tools"] == {"fast_fingers": True}
    assert payload["strategy_spec"]["setup_family"] == "momentum_expansion"


def test_build_backtest_plan_payload_preserves_explicit_empty_signal_rules():
    payload = _build_backtest_plan_payload(
        signal_mode="algo_ai",
        signal_rules=[],
        signal_logic="weird",
        signal_auto=False,
        tools=[],
    )

    assert payload["signal_rules"] == []
    assert payload["signal_logic"] == "AND"
    assert payload["setup_family"] == "pullback_continuation"


def test_build_backtest_plan_payload_preserves_none_signal_rules_as_default_ema():
    payload = _build_backtest_plan_payload(
        signal_mode="algo_ai",
        signal_rules=None,
        signal_logic="AND",
        signal_auto=False,
        tools=[],
    )

    assert payload["signal_rules"] == [{"source": "ema_crossover"}]
    assert payload["setup_family"] == "trend_continuation"


def test_extract_backtest_setup_family_and_source_are_spec_first():
    plan = json.dumps(
        {
            "signal_mode": "algo_only",
            "setup_family": "pullback_continuation",
            "source": "",
            "strategy_spec": {
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "metadata": {"source": "ui_ai_signal_card"},
            },
        }
    )

    assert _extract_backtest_setup_family(plan) == "discretionary_ai"
    assert _extract_backtest_source(plan) == "ui_ai_signal_card"


def test_extract_backtest_strategy_spec_returns_stored_spec():
    plan = json.dumps(
        {
            "strategy_spec": {
                "spec_version": "v1",
                "signal_mode": "algo_ai",
                "setup_family": "momentum_expansion",
            }
        }
    )

    assert _extract_backtest_strategy_spec(plan)["setup_family"] == "momentum_expansion"


def test_extract_backtest_strategy_spec_returns_canonical_spec_from_plan_payload():
    plan = json.dumps(
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

    spec = _extract_backtest_strategy_spec(plan)

    assert spec["spec_version"] == "v1"
    assert spec["signal_mode"] == "ai_signal"
    assert spec["setup_family"] == "discretionary_ai"
    assert spec["metadata"]["source"] == "backtest_runner"


def test_extract_backtest_tools_normalizes_dict_flags():
    plan = json.dumps(
        {
            "tools": {
                "fast_fingers": True,
                "bos_guard": False,
            }
        }
    )

    assert _extract_backtest_tools(plan) == ["fast_fingers"]


def test_extract_backtest_signal_rules_and_logic_normalize_legacy_nulls():
    plan = json.dumps(
        {
            "signal_rules": None,
            "signal_logic": None,
        }
    )

    assert _extract_backtest_signal_rules(plan) == [{"source": "ema_crossover"}]
    assert _extract_backtest_signal_logic(plan) == "AND"


def test_extract_backtest_signal_rules_fail_closed_on_malformed_shapes():
    plan = json.dumps(
        {
            "signal_rules": "ema_crossover",
            "signal_logic": "weird",
        }
    )

    assert _extract_backtest_signal_rules(plan) == []
    assert _extract_backtest_signal_logic(plan) == "AND"
