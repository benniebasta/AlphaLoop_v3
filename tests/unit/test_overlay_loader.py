from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alphaloop.trading.overlay_loader import (
    load_overlay_config,
    normalize_overlay_tools,
    save_overlay_config,
)


def test_normalize_overlay_tools_deduplicates_and_strips():
    assert normalize_overlay_tools([" tick_jump_guard ", "", "tick_jump_guard", "liq_vacuum_guard"]) == [
        "tick_jump_guard",
        "liq_vacuum_guard",
    ]


@pytest.mark.asyncio
async def test_load_overlay_config_normalizes_extra_tools():
    settings_service = SimpleNamespace(
        get=AsyncMock(return_value=json.dumps({
            "extra_tools": [" tick_jump_guard ", "tick_jump_guard", "liq_vacuum_guard"],
        }))
    )

    config = await load_overlay_config(settings_service, "XAUUSD", 21)

    assert config is not None
    assert config.extra_tools == ["tick_jump_guard", "liq_vacuum_guard"]


@pytest.mark.asyncio
async def test_save_overlay_config_persists_normalized_extra_tools():
    settings_service = SimpleNamespace(set=AsyncMock())

    config = await save_overlay_config(
        settings_service,
        "XAUUSD",
        21,
        [" tick_jump_guard ", "tick_jump_guard", "liq_vacuum_guard"],
    )

    assert config.extra_tools == ["tick_jump_guard", "liq_vacuum_guard"]
    assert settings_service.set.await_args.args[0] == "dry_run_overlay_XAUUSD_v21"
    assert json.loads(settings_service.set.await_args.args[1]) == {
        "extra_tools": ["tick_jump_guard", "liq_vacuum_guard"],
    }
