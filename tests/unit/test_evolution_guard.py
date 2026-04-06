from __future__ import annotations

import pytest

from alphaloop.core.config import EvolutionConfig
from alphaloop.research.evolution_guard import EvolutionGuard


@pytest.mark.asyncio
async def test_validate_oos_accepts_sharpe_alias():
    guard = EvolutionGuard(
        session_factory=None,
        event_bus=None,
        evolution_config=EvolutionConfig(),
    )

    result = await guard.validate_oos({
        "total_trades": 8,
        "win_rate": 0.5,
        "sharpe": 0.25,
    })

    assert result["passed"] is True
    assert result["reasons"] == []


@pytest.mark.asyncio
async def test_validate_oos_blocks_negative_sharpe_alias():
    guard = EvolutionGuard(
        session_factory=None,
        event_bus=None,
        evolution_config=EvolutionConfig(),
    )

    result = await guard.validate_oos({
        "total_trades": 8,
        "win_rate": 0.5,
        "sharpe": -0.1,
    })

    assert result["passed"] is False
    assert any("negative" in reason.lower() for reason in result["reasons"])
