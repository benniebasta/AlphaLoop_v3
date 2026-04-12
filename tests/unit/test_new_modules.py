"""
Unit tests for new modules:
- risk/guards.py (NearDedupGuard, PortfolioCapGuard)
- monitoring/watchdog.py
- seedlab/evolution.py
- backtester/asset_trainer.py
- utils/time.py (session score for hour)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# Risk Guards — New Guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestNearDedupGuard:
    def test_blocks_close_trade(self):
        from alphaloop.risk.guards import NearDedupGuard
        g = NearDedupGuard(min_atr_distance=1.0)
        assert g.is_too_close(
            proposed_entry=2000.0,
            atr=5.0,
            open_trades=[{"symbol": "XAUUSD", "entry_price": 2003.0}],
            symbol="XAUUSD",
        )

    def test_passes_distant_trade(self):
        from alphaloop.risk.guards import NearDedupGuard
        g = NearDedupGuard(min_atr_distance=1.0)
        assert not g.is_too_close(
            proposed_entry=2000.0,
            atr=5.0,
            open_trades=[{"symbol": "XAUUSD", "entry_price": 2010.0}],
            symbol="XAUUSD",
        )

    def test_ignores_other_symbols(self):
        from alphaloop.risk.guards import NearDedupGuard
        g = NearDedupGuard(min_atr_distance=1.0)
        assert not g.is_too_close(
            proposed_entry=2000.0,
            atr=5.0,
            open_trades=[{"symbol": "BTCUSD", "entry_price": 2001.0}],
            symbol="XAUUSD",
        )

    def test_handles_zero_atr(self):
        from alphaloop.risk.guards import NearDedupGuard
        g = NearDedupGuard()
        assert not g.is_too_close(2000, 0, [{"symbol": "X", "entry_price": 2000}], "X")


class TestPortfolioCapGuard:
    def test_blocks_over_cap(self):
        """Two same-symbol same-direction trades → corr-adj risk > cap."""
        from alphaloop.risk.guards import PortfolioCapGuard
        g = PortfolioCapGuard(max_portfolio_risk_pct=3.0)
        # Same symbol + same direction = high correlation (0.30 default)
        # Use larger risk amounts to push over the 3% cap on $10k balance
        assert g.is_capped(
            open_trades=[
                {"risk_amount_usd": 200, "symbol": "XAUUSD", "direction": "BUY"},
                {"risk_amount_usd": 200, "symbol": "XAUUSD", "direction": "BUY"},
            ],
            balance=10000.0,
        )

    def test_passes_under_cap(self):
        from alphaloop.risk.guards import PortfolioCapGuard
        g = PortfolioCapGuard(max_portfolio_risk_pct=6.0)
        assert not g.is_capped(
            open_trades=[{"risk_amount_usd": 100, "symbol": "XAUUSD", "direction": "BUY"}],
            balance=10000.0,
        )

    def test_blocks_zero_balance(self):
        from alphaloop.risk.guards import PortfolioCapGuard
        g = PortfolioCapGuard()
        assert g.is_capped([], 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Watchdog
# ═══════════════════════════════════════════════════════════════════════════════

class TestWatchdog:
    def test_init(self):
        from alphaloop.monitoring.watchdog import TradingWatchdog
        from alphaloop.monitoring.health import HealthCheck
        h = HealthCheck()
        w = TradingWatchdog(health_check=h)
        assert not w._running
        status = w.get_status()
        assert status["running"] is False

    @pytest.mark.asyncio
    async def test_check_no_heartbeat(self):
        from alphaloop.monitoring.watchdog import TradingWatchdog
        from alphaloop.monitoring.health import HealthCheck, ComponentStatus
        h = HealthCheck()
        w = TradingWatchdog(health_check=h, heartbeat_path="/nonexistent/heartbeat.json")
        await w._check()
        report = h.get_report()
        assert report["components"]["trading_loop"]["status"] == ComponentStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_check_stale_heartbeat(self):
        from alphaloop.monitoring.watchdog import TradingWatchdog
        from alphaloop.monitoring.health import HealthCheck, ComponentStatus
        import time

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"timestamp": time.time() - 700, "alive": True}, f)
            path = f.name

        try:
            h = HealthCheck()
            w = TradingWatchdog(health_check=h, heartbeat_path=path, stale_threshold=600)
            await w._check()
            report = h.get_report()
            assert report["components"]["trading_loop"]["status"] == ComponentStatus.DEGRADED
        finally:
            Path(path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_check_fresh_heartbeat(self):
        from alphaloop.monitoring.watchdog import TradingWatchdog
        from alphaloop.monitoring.health import HealthCheck, ComponentStatus
        import time

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({
                "timestamp": time.time(),
                "alive": True,
                "symbol": "XAUUSD",
                "cycle": 42,
                "risk_state": {},
                "circuit_breaker": {"open": False},
            }, f)
            path = f.name

        try:
            h = HealthCheck()
            w = TradingWatchdog(health_check=h, heartbeat_path=path)
            await w._check()
            report = h.get_report()
            assert report["components"]["trading_loop"]["status"] == ComponentStatus.HEALTHY
        finally:
            Path(path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Evolution
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvolution:
    def test_mutate_produces_different_seed(self):
        from alphaloop.seedlab.evolution import mutate_seed
        from alphaloop.seedlab.seed_generator import StrategySeed, compute_seed_hash
        import random
        random.seed(42)

        filters = ("bos_guard", "ema200_trend", "fvg_guard", "session_filter")
        seed = StrategySeed(
            seed_hash=compute_seed_hash(list(filters)),
            name="test", category="trend", filters=filters,
        )
        mutated = None
        for _ in range(50):
            mutated = mutate_seed(seed, mutation_rate=1.0)
            if mutated:
                break
        assert mutated is not None
        assert mutated.seed_hash != seed.seed_hash

    def test_crossover(self):
        from alphaloop.seedlab.evolution import crossover_seeds
        from alphaloop.seedlab.seed_generator import StrategySeed, compute_seed_hash
        import random
        random.seed(42)

        a = StrategySeed(
            seed_hash=compute_seed_hash(["bos_guard", "ema200_trend", "fvg_guard"]),
            name="A", category="trend",
            filters=("bos_guard", "ema200_trend", "fvg_guard"),
        )
        b = StrategySeed(
            seed_hash=compute_seed_hash(["session_filter", "volatility_filter", "vwap_guard"]),
            name="B", category="scalp",
            filters=("session_filter", "volatility_filter", "vwap_guard"),
        )
        child = None
        for _ in range(50):
            child = crossover_seeds(a, b)
            if child:
                break
        assert child is not None
        assert child.seed_hash != a.seed_hash
        assert child.seed_hash != b.seed_hash

    def test_evolve_generation(self):
        from alphaloop.seedlab.evolution import evolve_generation
        from alphaloop.seedlab.seed_generator import generate_template_seeds
        import random
        random.seed(42)

        seeds = generate_template_seeds()
        scored = [(s, float(i) * 0.1) for i, s in enumerate(seeds)]
        next_gen = evolve_generation(scored, population_size=8, elite_count=2)
        assert len(next_gen) >= 2  # at least elites


# ═══════════════════════════════════════════════════════════════════════════════
# Session Score for Hour
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionScoreForHour:
    def test_overlap(self):
        from alphaloop.utils.time import get_session_score_for_hour
        assert get_session_score_for_hour(14) == 1.0
        assert get_session_score_for_hour(15) == 1.0

    def test_london(self):
        from alphaloop.utils.time import get_session_score_for_hour
        assert get_session_score_for_hour(9) == 0.85
        assert get_session_score_for_hour(12) == 0.85

    def test_ny(self):
        from alphaloop.utils.time import get_session_score_for_hour
        assert get_session_score_for_hour(17) == 0.85
        assert get_session_score_for_hour(20) == 0.85

    def test_asia_late(self):
        from alphaloop.utils.time import get_session_score_for_hour
        assert get_session_score_for_hour(5) == 0.40

    def test_asia_early(self):
        from alphaloop.utils.time import get_session_score_for_hour
        assert get_session_score_for_hour(2) == 0.20


# ═══════════════════════════════════════════════════════════════════════════════
# Strategy Version Creation
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategyVersionCreation:
    def test_creates_file(self):
        from alphaloop.backtester.asset_trainer import create_strategy_version
        from alphaloop.backtester.params import BacktestParams
        import alphaloop.backtester.asset_trainer as at

        with tempfile.TemporaryDirectory() as tmpdir:
            original = at.STRATEGY_VERSIONS_DIR
            at.STRATEGY_VERSIONS_DIR = Path(tmpdir)
            try:
                result = create_strategy_version(
                    symbol="TEST",
                    params=BacktestParams(),
                    metrics={"total_trades": 50, "win_rate": 0.5, "sharpe": 1.0},
                    tools=["ema200_filter"],
                )
                assert result["version"] == 1
                assert Path(result["_path"]).exists()
                data = json.loads(Path(result["_path"]).read_text())
                assert data["symbol"] == "TEST"
            finally:
                at.STRATEGY_VERSIONS_DIR = original

    def test_increments_version(self):
        # Each call generates a unique name → each starts at v1 (independent lineage).
        # To get v2, pass the same name explicitly (autolearn scenario).
        from alphaloop.backtester.asset_trainer import create_strategy_version
        from alphaloop.backtester.params import BacktestParams
        import alphaloop.backtester.asset_trainer as at

        with tempfile.TemporaryDirectory() as tmpdir:
            original = at.STRATEGY_VERSIONS_DIR
            at.STRATEGY_VERSIONS_DIR = Path(tmpdir)
            try:
                r1 = create_strategy_version("SYM", BacktestParams(), {}, [])
                r2 = create_strategy_version("SYM", BacktestParams(), {}, [])
                # Two fresh cards → different generated names → both v1
                assert r1["version"] == 1
                assert r2["version"] == 1
                assert r1["name"] != r2["name"]
                # Evolving same lineage (autolearn): pass the existing name → v2
                r3 = create_strategy_version("SYM", BacktestParams(), {}, [], name=r1["name"])
                assert r3["version"] == 2
            finally:
                at.STRATEGY_VERSIONS_DIR = original
