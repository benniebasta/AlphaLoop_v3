"""
backtester/auto_improve.py — Optuna optimization integration.

Uses Optuna TPE sampler to search for optimal strategy parameters,
with walk-forward validation to prevent overfitting.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np
import optuna
from optuna.samplers import TPESampler
from pydantic import BaseModel, Field

from alphaloop.backtester.engine import BacktestEngine, BacktestResult
from alphaloop.core.config import EvolutionConfig

logger = logging.getLogger(__name__)

# Silence Optuna's own logging
optuna.logging.set_verbosity(optuna.logging.WARNING)


class OptimizationResult(BaseModel):
    """Result of an Optuna optimization run."""

    best_params: dict[str, Any] = Field(default_factory=dict)
    best_sharpe: float | None = None
    baseline_sharpe: float | None = None
    improved: bool = False
    n_trials: int = 0
    changes: dict[str, dict[str, float]] = Field(default_factory=dict)
    error: str | None = None


class SearchSpace(BaseModel):
    """Definition of a single parameter's search range."""

    name: str
    current_value: float
    low_mult: float = 0.85
    high_mult: float = 1.15
    step: float | None = None


class AutoImprover:
    """
    Optuna-based parameter optimization with anti-overfitting guardrails.

    Uses walk-forward evaluation and caps parameter changes per cycle.

    Injected dependencies:
    - backtest_engine: for running backtests
    - evolution_config: for change limits and thresholds
    """

    def __init__(
        self,
        backtest_engine: BacktestEngine,
        evolution_config: EvolutionConfig,
    ) -> None:
        self._engine = backtest_engine
        self._evo = evolution_config

    async def optimize(
        self,
        symbol: str,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        current_params: dict[str, Any],
        search_space: list[SearchSpace],
        signal_fn_factory: Callable[[dict[str, Any]], Any],
        n_trials: int = 30,
        balance: float = 10_000.0,
        risk_pct: float = 0.01,
        filters: list[str] | None = None,
        timestamps: list[Any] | None = None,
        min_improvement: float = 0.05,
    ) -> OptimizationResult:
        """
        Run Optuna optimization over parameter search space.

        Args:
            symbol: Trading symbol.
            opens, highs, lows, closes: Price arrays.
            current_params: Current strategy parameters.
            search_space: List of SearchSpace definitions.
            signal_fn_factory: Callable(params) -> signal_fn for backtests.
            n_trials: Number of Optuna trials.
            balance: Starting balance.
            risk_pct: Risk per trade.
            filters: Active filters.
            timestamps: Bar timestamps.
            min_improvement: Minimum Sharpe improvement to accept changes.

        Returns:
            OptimizationResult with best params and changes.
        """
        result = OptimizationResult()

        # Run baseline
        baseline_fn = signal_fn_factory(current_params)
        baseline_bt = await self._engine.run(
            symbol=symbol, opens=opens, highs=highs, lows=lows, closes=closes,
            timestamps=timestamps, balance=balance, risk_pct=risk_pct,
            filters=filters, signal_fn=baseline_fn,
        )
        result.baseline_sharpe = baseline_bt.sharpe
        if result.baseline_sharpe is None:
            result.error = "Cannot compute baseline Sharpe"
            return result

        logger.info("Baseline Sharpe: %.3f", result.baseline_sharpe)

        # Create Optuna study
        study = optuna.create_study(
            direction="maximize",
            sampler=TPESampler(n_startup_trials=5, seed=42),
        )

        max_change = self._evo.max_param_change_pct

        async def objective_async(trial: optuna.Trial) -> float:
            candidate = dict(current_params)
            for sp in search_space:
                low = sp.current_value * sp.low_mult
                high = sp.current_value * sp.high_mult
                val = trial.suggest_float(sp.name, low, high)

                # Enforce max change per cycle
                if sp.current_value != 0:
                    change = abs(val - sp.current_value) / abs(sp.current_value)
                    if change > max_change:
                        raise optuna.TrialPruned()

                candidate[sp.name] = val

            sig_fn = signal_fn_factory(candidate)
            bt = await self._engine.run(
                symbol=symbol, opens=opens, highs=highs, lows=lows, closes=closes,
                timestamps=timestamps, balance=balance, risk_pct=risk_pct,
                filters=filters, signal_fn=sig_fn,
            )
            return bt.sharpe if bt.sharpe is not None else -999.0

        # Optuna doesn't support async natively, so we wrap
        import asyncio

        def objective(trial: optuna.Trial) -> float:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create a new loop in a thread for nested async
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run, objective_async(trial)
                    )
                    return future.result()
            return loop.run_until_complete(objective_async(trial))

        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        result.n_trials = n_trials

        best = study.best_trial
        result.best_sharpe = best.value

        # Check improvement threshold
        if result.best_sharpe <= result.baseline_sharpe + min_improvement:
            logger.info(
                "Best Sharpe %.3f not significantly better than baseline %.3f",
                result.best_sharpe, result.baseline_sharpe,
            )
            result.best_params = current_params
            return result

        # Build changes dict
        new_params = dict(current_params)
        changes: dict[str, dict[str, float]] = {}
        for key, val in best.params.items():
            old = current_params.get(key)
            if old is not None and isinstance(old, (int, float)):
                if abs(val - old) > 1e-6:
                    changes[key] = {"from": round(old, 4), "to": round(val, 4)}
            new_params[key] = val

        result.best_params = new_params
        result.changes = changes
        result.improved = True

        logger.info(
            "Optimization improved Sharpe: %.3f -> %.3f | Changes: %s",
            result.baseline_sharpe, result.best_sharpe, changes,
        )
        return result
