"""
backtester/parallel_backtest.py — Run multiple backtests concurrently.

Provides utilities for running backtests in parallel using asyncio,
with configurable concurrency limits and result aggregation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine

from alphaloop.backtester.engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 4


class ParallelBacktester:
    """
    Runs multiple backtests concurrently with bounded parallelism.

    Uses asyncio.Semaphore to limit concurrent backtests and prevent
    excessive memory/CPU usage.
    """

    def __init__(
        self,
        engine: BacktestEngine,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._engine = engine
        self._max_concurrent = max_concurrent

    async def run_batch(
        self,
        tasks: list[dict[str, Any]],
        progress_callback: Callable[[int, int], Any] | None = None,
    ) -> list[BacktestResult]:
        """
        Run a batch of backtests concurrently.

        Args:
            tasks: List of dicts, each containing kwargs for BacktestEngine.run().
            progress_callback: Optional (completed, total) callback.

        Returns:
            List of BacktestResult in the same order as input tasks.
        """
        semaphore = asyncio.Semaphore(self._max_concurrent)
        completed = 0
        total = len(tasks)
        results: list[BacktestResult | None] = [None] * total

        async def run_one(idx: int, kwargs: dict[str, Any]) -> None:
            nonlocal completed
            async with semaphore:
                try:
                    result = await self._engine.run(**kwargs)
                    results[idx] = result
                except Exception as exc:
                    logger.error("Parallel backtest %d failed: %s", idx, exc)
                    results[idx] = BacktestResult(
                        run_id=kwargs.get("run_id", f"parallel_{idx}"),
                        symbol=kwargs.get("symbol", ""),
                        error=str(exc),
                    )
                finally:
                    completed += 1
                    if progress_callback:
                        try:
                            progress_callback(completed, total)
                        except Exception:
                            pass

        coros = [run_one(i, task) for i, task in enumerate(tasks)]
        await asyncio.gather(*coros)

        return [r for r in results if r is not None]

    async def run_parameter_sweep(
        self,
        base_kwargs: dict[str, Any],
        param_variants: list[dict[str, Any]],
        signal_fn_factory: Callable[[dict[str, Any]], Any],
    ) -> list[tuple[dict[str, Any], BacktestResult]]:
        """
        Sweep over parameter variants, running a backtest for each.

        Args:
            base_kwargs: Common kwargs for all backtests (symbol, data, etc.).
            param_variants: List of parameter dicts to test.
            signal_fn_factory: Callable(params) -> signal_fn.

        Returns:
            List of (params, BacktestResult) tuples.
        """
        tasks: list[dict[str, Any]] = []
        for i, params in enumerate(param_variants):
            kwargs = dict(base_kwargs)
            kwargs["signal_fn"] = signal_fn_factory(params)
            kwargs["run_id"] = f"sweep_{i}"
            tasks.append(kwargs)

        results = await self.run_batch(tasks)

        paired: list[tuple[dict[str, Any], BacktestResult]] = []
        for params, result in zip(param_variants, results):
            paired.append((params, result))

        # Sort by Sharpe descending
        paired.sort(
            key=lambda x: x[1].sharpe if x[1].sharpe is not None else -999,
            reverse=True,
        )

        return paired

    async def run_multi_symbol(
        self,
        symbols: list[str],
        data_loader: Callable[[str], Coroutine[Any, Any, dict[str, Any]]],
        common_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, BacktestResult]:
        """
        Run backtests across multiple symbols concurrently.

        Args:
            symbols: List of trading symbols.
            data_loader: Async callable(symbol) -> dict with opens, highs, lows, closes.
            common_kwargs: Common kwargs for all backtests.

        Returns:
            Dict mapping symbol -> BacktestResult.
        """
        semaphore = asyncio.Semaphore(self._max_concurrent)
        results: dict[str, BacktestResult] = {}

        async def run_symbol(symbol: str) -> None:
            async with semaphore:
                try:
                    data = await data_loader(symbol)
                    kwargs = dict(common_kwargs or {})
                    kwargs.update(data)
                    kwargs["symbol"] = symbol
                    kwargs["run_id"] = f"multi_{symbol}"
                    result = await self._engine.run(**kwargs)
                    results[symbol] = result
                except Exception as exc:
                    logger.error("Multi-symbol backtest for %s failed: %s", symbol, exc)
                    results[symbol] = BacktestResult(
                        symbol=symbol, error=str(exc),
                    )

        await asyncio.gather(*(run_symbol(s) for s in symbols))
        return results
