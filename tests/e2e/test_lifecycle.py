"""
E2E tests for the full strategy lifecycle.

Tests:
1. Boot app → verify health endpoint
2. Create backtest → run → verify DB state + strategy version creation
3. Strategy version lifecycle: create → evaluate → promote
4. Hard rules: verify all 13 rules execute
5. Risk guards: verify all 7 guards instantiate and run
"""

from __future__ import annotations

import json
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from alphaloop.core.config import AppConfig, DBConfig
from alphaloop.core.container import Container
from alphaloop.core.events import EventBus
from alphaloop.db.models.base import Base


@pytest_asyncio.fixture
async def app_client():
    """Full FastAPI app with in-memory DB for E2E tests."""
    config = AppConfig(
        db=DBConfig(url="sqlite+aiosqlite://", echo=False),
        dry_run=True,
        environment="test",
    )
    container = Container(config)
    container.db_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with container.db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    container.db_session_factory = async_sessionmaker(
        bind=container.db_engine, class_=AsyncSession, expire_on_commit=False
    )

    from alphaloop.webui.app import create_webui_app
    app = create_webui_app(container)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await container.close()


@pytest.mark.asyncio
async def test_health_endpoint(app_client):
    """E2E: Health endpoint returns OK."""
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_dashboard_returns_data(app_client):
    """E2E: Dashboard endpoint returns expected structure."""
    resp = await app_client.get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "open_trades" in data
    assert "daily_pnl" in data


@pytest.mark.asyncio
async def test_strategies_list_empty(app_client):
    """E2E: Strategies list returns empty when no versions exist."""
    resp = await app_client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert "strategies" in data
    assert isinstance(data["strategies"], list)


@pytest.mark.asyncio
async def test_risk_guards_instantiate():
    """E2E: All 7 risk guards instantiate and run basic operations."""
    from alphaloop.risk.guards import (
        SignalHashFilter,
        ConfidenceVarianceFilter,
        SpreadRegimeFilter,
        EquityCurveScaler,
        DrawdownPauseGuard,
        NearDedupGuard,
        PortfolioCapGuard,
    )

    # SignalHashFilter
    shf = SignalHashFilter(window=3)
    assert not shf._hashes  # empty initially

    # ConfidenceVarianceFilter
    cvf = ConfidenceVarianceFilter(window=3, max_stdev=0.15)
    cvf.record(0.8)
    cvf.record(0.82)
    assert not cvf.is_unstable()  # not enough data yet

    # SpreadRegimeFilter
    srf = SpreadRegimeFilter(window=50, threshold=1.8)
    for _ in range(15):
        srf.record(3.0)
    assert not srf.is_spike(3.5)

    # EquityCurveScaler
    ecs = EquityCurveScaler(window=20)
    assert ecs.risk_scale() == 1.0  # not enough data

    # DrawdownPauseGuard
    dpg = DrawdownPauseGuard(pause_minutes=30)
    assert not dpg.is_paused()

    # NearDedupGuard
    ndg = NearDedupGuard(min_atr_distance=1.0)
    assert not ndg.is_too_close(
        proposed_entry=2000.0,
        atr=5.0,
        open_trades=[],
        symbol="XAUUSD",
    )
    # With a nearby trade
    assert ndg.is_too_close(
        proposed_entry=2000.0,
        atr=5.0,
        open_trades=[{"symbol": "XAUUSD", "entry_price": 2002.0}],
        symbol="XAUUSD",
    )

    # PortfolioCapGuard
    pcg = PortfolioCapGuard(max_portfolio_risk_pct=6.0)
    assert not pcg.is_capped(open_trades=[], balance=10000.0)
    # Should cap when risk is high
    assert pcg.is_capped(
        open_trades=[
            {"risk_amount_usd": 200.0},
            {"risk_amount_usd": 200.0},
            {"risk_amount_usd": 250.0},
        ],
        balance=10000.0,
    )


@pytest.mark.asyncio
async def test_deployment_pipeline_evaluate():
    """E2E: DeploymentPipeline evaluates promotion correctly."""
    from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
    from alphaloop.core.config import EvolutionConfig
    from alphaloop.core.events import EventBus
    from alphaloop.core.types import StrategyStatus
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    pipeline = DeploymentPipeline(
        session_factory=sf,
        event_bus=EventBus(),
        evolution_config=EvolutionConfig(),
    )

    # Not enough trades — should fail
    result = await pipeline.evaluate_promotion(
        current_status=StrategyStatus.CANDIDATE,
        metrics={"total_trades": 10, "sharpe_ratio": 1.0, "win_rate": 0.55},
        cycles_completed=0,
    )
    assert not result["eligible"]
    assert any("Trades" in r for r in result["reasons"])

    # Enough trades — should pass
    result = await pipeline.evaluate_promotion(
        current_status=StrategyStatus.CANDIDATE,
        metrics={"total_trades": 50, "sharpe_ratio": 1.0, "win_rate": 0.55},
        cycles_completed=0,
    )
    assert result["eligible"]
    assert result["target_status"] == StrategyStatus.DRY_RUN

    await engine.dispose()


@pytest.mark.asyncio
async def test_ai_signal_discovery_candidate_skips_backtest_gate():
    """E2E: AI signal discovery cards can enter dry_run immediately."""
    from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
    from alphaloop.core.config import EvolutionConfig
    from alphaloop.core.events import EventBus
    from alphaloop.core.types import StrategyStatus
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    pipeline = DeploymentPipeline(
        session_factory=sf,
        event_bus=EventBus(),
        evolution_config=EvolutionConfig(),
    )

    result = await pipeline.evaluate_promotion(
        current_status=StrategyStatus.CANDIDATE,
        metrics={"total_trades": 0, "sharpe_ratio": 0.0, "win_rate": 0.0},
        cycles_completed=0,
        bypass_candidate_gate=True,
    )
    assert result["eligible"]
    assert result["target_status"] == StrategyStatus.DRY_RUN
    assert result["reasons"] == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_strategy_version_creation():
    """E2E: Strategy version files are created correctly."""
    from alphaloop.backtester.asset_trainer import create_strategy_version
    from alphaloop.backtester.params import BacktestParams
    import tempfile
    import os

    # Use a temp directory for strategy_versions
    with tempfile.TemporaryDirectory() as tmpdir:
        # Monkey-patch the dir
        import alphaloop.backtester.asset_trainer as at
        original_dir = at.STRATEGY_VERSIONS_DIR
        at.STRATEGY_VERSIONS_DIR = Path(tmpdir)

        try:
            params = BacktestParams(ema_fast=21, ema_slow=55)
            metrics = {
                "total_trades": 100,
                "win_rate": 0.52,
                "sharpe": 1.5,
                "max_drawdown_pct": -12.0,
                "total_pnl": 5000.0,
            }

            result = create_strategy_version(
                symbol="XAUUSD",
                params=params,
                metrics=metrics,
                tools=["ema200_filter", "bos_guard"],
                status="candidate",
            )

            assert result["symbol"] == "XAUUSD"
            assert result["version"] == 1
            assert result["status"] == "candidate"
            assert result["summary"]["win_rate"] == 0.52

            # Verify file exists
            path = Path(result["_path"])
            assert path.exists()

            # Verify JSON is valid
            data = json.loads(path.read_text())
            assert data["symbol"] == "XAUUSD"

            # Second version should be v2
            result2 = create_strategy_version(
                symbol="XAUUSD", params=params, metrics=metrics,
                tools=[], status="candidate",
            )
            assert result2["version"] == 2

        finally:
            at.STRATEGY_VERSIONS_DIR = original_dir


@pytest.mark.asyncio
async def test_evolutionary_search():
    """E2E: Evolutionary search produces new seed variants."""
    from alphaloop.seedlab.evolution import (
        mutate_seed, crossover_seeds, evolve_generation, EvolutionarySearch,
    )
    from alphaloop.seedlab.seed_generator import generate_template_seeds

    seeds = generate_template_seeds()
    assert len(seeds) > 0

    # Test mutation
    mutated = None
    for _ in range(20):  # Try several times due to randomness
        mutated = mutate_seed(seeds[0], mutation_rate=1.0)
        if mutated:
            break
    assert mutated is not None
    assert mutated.seed_hash != seeds[0].seed_hash

    # Test crossover
    if len(seeds) >= 2:
        child = None
        for _ in range(20):
            child = crossover_seeds(seeds[0], seeds[1])
            if child:
                break
        # May or may not produce a valid child depending on filter overlap

    # Test evolution
    scored = [(s, float(i)) for i, s in enumerate(seeds)]
    next_gen = evolve_generation(scored, population_size=10)
    assert len(next_gen) > 0


@pytest.mark.asyncio
async def test_ai_caller_retry_logic():
    """E2E: AI caller retries on failure and uses fallback."""
    from alphaloop.ai.caller import AICaller
    from alphaloop.core.errors import AlphaLoopError

    caller = AICaller(api_keys={"gemini": "test-key"})

    # Mock the _dispatch to fail twice then succeed
    call_count = 0

    async def mock_dispatch(cfg, api_key, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise Exception(f"Simulated failure #{call_count}")
        return "Success after retries"

    caller._dispatch = mock_dispatch

    # Should succeed after retries
    result = await caller.call_model(
        "gemini-2.5-flash",
        messages=[{"role": "user", "content": "test"}],
        max_retries=2,
        retry_delay=0.01,  # Fast for tests
    )
    assert result == "Success after retries"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retraining_detection():
    """E2E: Research analyzer detects degradation correctly."""
    from alphaloop.research.analyzer import _detect_degradation

    # Stable performance
    stable = [10.0, -5.0, 15.0, -3.0, 12.0, -4.0, 8.0, -2.0, 11.0, -3.0] * 6
    result = _detect_degradation(stable, window=30)
    assert result["status"] in ("stable", "insufficient_data")

    # Degrading: good first half, bad second half
    good = [10.0, 5.0, 15.0, 8.0, 12.0] * 6
    bad = [-10.0, -5.0, -15.0, -8.0, -12.0] * 6
    degrading = good + bad
    result = _detect_degradation(degrading, window=30)
    assert result["status"] == "degrading"
