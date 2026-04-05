from __future__ import annotations

from pathlib import Path

from alphaloop.backtester import asset_trainer
from alphaloop.core.constants import STRATEGY_VERSIONS_DIR as CORE_STRATEGY_VERSIONS_DIR
from alphaloop.webui.routes import strategies as strategies_route


def test_strategy_versions_dir_is_shared_and_absolute():
    assert CORE_STRATEGY_VERSIONS_DIR.is_absolute()
    assert asset_trainer.STRATEGY_VERSIONS_DIR == CORE_STRATEGY_VERSIONS_DIR
    assert strategies_route.STRATEGY_VERSIONS_DIR == CORE_STRATEGY_VERSIONS_DIR
    assert CORE_STRATEGY_VERSIONS_DIR == Path(__file__).resolve().parents[2] / "strategy_versions"
