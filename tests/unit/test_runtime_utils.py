from types import SimpleNamespace

from alphaloop.trading.runtime_utils import (
    current_runtime_strategy,
    current_strategy_reference,
)


def test_current_runtime_strategy_prefers_cached_runtime_snapshot():
    runtime = current_runtime_strategy(
        runtime_strategy={"version": 11, "spec_version": "v1"},
        active_strategy=SimpleNamespace(version="legacy"),
    )

    assert runtime == {"version": 11, "spec_version": "v1"}


def test_current_strategy_reference_prefers_cached_runtime_snapshot():
    reference = current_strategy_reference(
        symbol="XAUUSD",
        runtime_strategy={"version": 11, "spec_version": "v1"},
        active_strategy=SimpleNamespace(version="legacy"),
    )

    assert reference["strategy_id"] == "XAUUSD.v11"
    assert reference["strategy_version"] == "11"


def test_current_strategy_reference_falls_back_to_active_strategy():
    reference = current_strategy_reference(
        symbol="XAUUSD",
        active_strategy=SimpleNamespace(version=7),
    )

    assert reference["strategy_id"] == "XAUUSD.v7"
    assert reference["strategy_version"] == "7"
