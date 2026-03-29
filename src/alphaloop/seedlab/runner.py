"""
seedlab/runner.py — Pipeline orchestrator.

Entry point for the full SeedLab pipeline:
1. Generate seeds (template + optional combinatorial)
2. Run each seed through multi-regime backtest
3. Extract metrics
4. Analyze stability
5. Score and rank
6. Build strategy cards
7. Save to registry
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from pydantic import BaseModel, Field

from alphaloop.seedlab.metrics import SeedMetrics
from alphaloop.seedlab.ranking import SeedScore, rank_seeds, score_seed
from alphaloop.seedlab.regime_runner import RegimeRunner
from alphaloop.seedlab.registry import CardRegistry
from alphaloop.seedlab.seed_generator import (
    StrategySeed,
    generate_combinatorial_seeds,
    generate_template_seeds,
)
from alphaloop.seedlab.stability import StabilityReport, analyze_stability
from alphaloop.seedlab.strategy_card import StrategyCard, build_strategy_card

logger = logging.getLogger(__name__)

# Type for progress callback: (phase, current, total) -> None
ProgressCallback = Callable[[str, int, int], Any]


class SeedLabConfig(BaseModel):
    """Configuration for a SeedLab run."""

    symbol: str
    days: int = 365
    balance: float = 10_000.0
    backtest_risk_factor: float = 0.85
    min_regime_bars: int = 100
    use_template_seeds: bool = True
    use_combinatorial_seeds: bool = False
    max_combinatorial_seeds: int = 30
    stability_overrides: dict[str, Any] | None = None
    max_parallel: int = 4  # Max concurrent seed evaluations


class SeedLabResult(BaseModel):
    """Final output of a SeedLab run."""

    run_id: str
    symbol: str
    total_seeds: int = 0
    evaluated_seeds: int = 0
    passed_seeds: int = 0
    rejected_seeds: int = 0
    cards: list[StrategyCard] = Field(default_factory=list)
    ranked_scores: list[SeedScore] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "symbol": self.symbol,
            "total_seeds": self.total_seeds,
            "evaluated": self.evaluated_seeds,
            "passed": self.passed_seeds,
            "rejected": self.rejected_seeds,
            "top_score": self.ranked_scores[0].total_score if self.ranked_scores else 0,
            "elapsed_s": round(self.elapsed_seconds, 1),
        }


class SeedLabRunner:
    """
    Async pipeline orchestrator for strategy discovery.

    Injected dependencies:
    - regime_runner: for multi-regime backtesting
    - registry: for saving strategy cards
    """

    def __init__(
        self,
        regime_runner: RegimeRunner | None = None,
        registry: CardRegistry | None = None,
    ) -> None:
        self._regime_runner = regime_runner or RegimeRunner()
        self._registry = registry or CardRegistry()

    async def run(
        self,
        config: SeedLabConfig,
        backtest_fn: Any,
        highs: Any,
        lows: Any,
        closes: Any,
        params: dict[str, Any] | None = None,
        run_id: str | None = None,
        stop_check: Callable[[], bool] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> SeedLabResult:
        """
        Execute the full SeedLab pipeline.

        Args:
            config: SeedLabConfig with symbol, balance, thresholds.
            backtest_fn: Async callable for backtesting a seed.
            highs, lows, closes: Price arrays for regime detection.
            params: Strategy parameters dict.
            run_id: Optional run ID (auto-generated if not provided).
            stop_check: Callable returning True to abort.
            progress_callback: Optional (phase, current, total) callback.

        Returns:
            SeedLabResult with all cards, scores, and summary.
        """
        if run_id is None:
            run_id = f"seedlab_{config.symbol}_{int(time.time())}"

        result = SeedLabResult(run_id=run_id, symbol=config.symbol)
        t0 = time.monotonic()

        try:
            # Phase 1: Generate seeds
            self._report(progress_callback, "generating_seeds", 0, 1)
            seeds = self._generate_seeds(config)
            result.total_seeds = len(seeds)

            if not seeds:
                result.error = "No valid seeds generated"
                return result

            # Phase 2: Evaluate seeds (parallel with bounded concurrency)
            cards: list[StrategyCard] = []
            scores: list[SeedScore] = []
            semaphore = asyncio.Semaphore(config.max_parallel)
            eval_lock = asyncio.Lock()  # protects shared state updates

            async def evaluate_one(idx: int, seed: StrategySeed) -> None:
                """Evaluate a single seed under semaphore control."""
                if stop_check and stop_check():
                    return

                async with semaphore:
                    if stop_check and stop_check():
                        return

                    self._report(progress_callback, "evaluating", idx + 1, len(seeds))

                    bt_result = await self._regime_runner.run_seed(
                        seed=seed,
                        backtest_fn=backtest_fn,
                        highs=highs,
                        lows=lows,
                        closes=closes,
                    )

                    async with eval_lock:
                        result.evaluated_seeds += 1

                    if not bt_result.success:
                        logger.warning("Seed %s failed: %s", seed.name, bt_result.error)
                        return

                    stability = analyze_stability(
                        full_metrics=bt_result.full_metrics,
                        regime_metrics=bt_result.regime_metrics,
                        thresholds=config.stability_overrides,
                    )

                    seed_score = score_seed(
                        bt_result.full_metrics, stability, seed_name=seed.name,
                    )

                    card = build_strategy_card(
                        name=seed.name,
                        symbol=config.symbol,
                        category=seed.category,
                        filters=list(seed.filters),
                        params=params or {},
                        full_metrics=bt_result.full_metrics,
                        regime_metrics=bt_result.regime_metrics,
                        stability=stability,
                        score=seed_score,
                        backtest_bars=len(closes) if hasattr(closes, "__len__") else 0,
                        backtest_days=config.days,
                    )

                    async with eval_lock:
                        scores.append(seed_score)
                        cards.append(card)
                        self._registry.save(card)
                        if stability.passed:
                            result.passed_seeds += 1
                        else:
                            result.rejected_seeds += 1

            # Run all evaluations concurrently (bounded by semaphore)
            tasks = [evaluate_one(i, seed) for i, seed in enumerate(seeds)]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Phase 3: Rank
            self._report(progress_callback, "ranking", 0, 1)
            result.ranked_scores = rank_seeds(scores)
            result.cards = cards

        except Exception as exc:
            result.error = str(exc)
            logger.error("SeedLab run failed: %s", exc, exc_info=True)

        result.elapsed_seconds = time.monotonic() - t0
        logger.info(
            "SeedLab %s complete: %d evaluated, %d passed, %d rejected (%.1fs)",
            run_id, result.evaluated_seeds, result.passed_seeds,
            result.rejected_seeds, result.elapsed_seconds,
        )
        return result

    def _generate_seeds(self, config: SeedLabConfig) -> list[StrategySeed]:
        """Generate seeds based on configuration."""
        seeds: list[StrategySeed] = []

        if config.use_template_seeds:
            seeds.extend(generate_template_seeds())
            logger.info("Generated %d template seeds", len(seeds))

        if config.use_combinatorial_seeds:
            existing_hashes = {s.seed_hash for s in seeds}
            combo = generate_combinatorial_seeds(
                max_seeds=config.max_combinatorial_seeds,
            )
            for c in combo:
                if c.seed_hash not in existing_hashes:
                    seeds.append(c)
                    existing_hashes.add(c.seed_hash)
            logger.info("Total seeds after combinatorial: %d", len(seeds))

        return seeds

    @staticmethod
    def _report(cb: ProgressCallback | None, phase: str, current: int, total: int) -> None:
        if cb:
            try:
                cb(phase, current, total)
            except Exception:
                pass
