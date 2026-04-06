from __future__ import annotations

import pytest

from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
from alphaloop.core.types import StrategyStatus


class _FakeSession:
    def add(self, obj):
        return None

    async def flush(self):
        return None

    async def commit(self):
        return None


class _FakeSessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_evaluate_promotion_honors_max_dd_pct_alias():
    pipeline = DeploymentPipeline(
        session_factory=None,
        event_bus=None,
        evolution_config=None,
    )

    result = await pipeline.evaluate_promotion(
        current_status=StrategyStatus.CANDIDATE,
        metrics={
            "total_trades": 50,
            "sharpe": 1.2,
            "win_rate": 0.5,
            "max_dd_pct": -30.0,
        },
    )

    assert result["eligible"] is False
    assert any("Drawdown -30.0% exceeds limit -25.0%" in reason for reason in result["reasons"])


@pytest.mark.asyncio
async def test_evaluate_promotion_accepts_holdout_sharpe_ratio_alias():
    pipeline = DeploymentPipeline(
        session_factory=None,
        event_bus=None,
        evolution_config=None,
    )

    result = await pipeline.evaluate_promotion(
        current_status=StrategyStatus.DEMO,
        metrics={
            "total_trades": 120,
            "sharpe_ratio": 0.9,
            "win_rate": 0.5,
            "max_drawdown_pct": -10.0,
        },
        cycles_completed=5,
        holdout_result={"sharpe_ratio": 0.35},
    )

    assert result["eligible"] is True
    assert result["target_status"] == StrategyStatus.LIVE


@pytest.mark.asyncio
async def test_end_canary_accepts_sharpe_ratio_alias():
    pipeline = DeploymentPipeline(
        session_factory=_FakeSessionFactory(),
        event_bus=None,
        evolution_config=None,
    )

    result = await pipeline.end_canary(
        symbol="XAUUSD",
        strategy_version="v7",
        canary_id="canary-test",
        metrics={
            "sharpe_ratio": 0.4,
            "win_rate": 0.5,
            "total_trades": 7,
        },
    )

    assert result["recommendation"] == "promote"
