from __future__ import annotations

from pathlib import Path

from alphaloop.backtester import asset_trainer
from alphaloop.backtester.params import BacktestParams
from alphaloop.trading.strategy_loader import normalize_signal_mode


def test_normalize_signal_mode_accepts_canonical_values():
    assert normalize_signal_mode("algo_only") == "algo_only"
    assert normalize_signal_mode("algo_ai") == "algo_ai"
    assert normalize_signal_mode("ai_signal") == "ai_signal"


def test_normalize_signal_mode_defaults_unknown_to_ai_signal():
    assert normalize_signal_mode(None) == "ai_signal"
    assert normalize_signal_mode("legacy_mode") == "ai_signal"


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
