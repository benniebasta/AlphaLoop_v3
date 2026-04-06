"""
seedlab/background_runner.py — Background task execution for SeedLab runs.

Mirrors the pattern from backtester/runner.py:
- In-memory task tracking (_tasks, _stop_flags, _logs)
- Graceful stop via flags
- Log buffering with streaming endpoint support
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

# ── In-memory state ─────────────────────────────────────────────────────────
_tasks: dict[str, asyncio.Task] = {}
_stop_flags: dict[str, bool] = {}
_logs: dict[str, list[str]] = defaultdict(list)
_MAX_LOG_LINES = 500


def _seedlab_backtest_params(filters: list[str]) -> "BacktestParams":
    """Build per-seed backtest params so setup identity follows the candidate's filters."""
    from alphaloop.backtester.runner import _base_backtest_params
    from alphaloop.backtester.params import BacktestParams

    params = _base_backtest_params(
        signal_mode="algo_ai",
        signal_rules=None,
        signal_logic="AND",
        signal_auto=False,
        tools=filters,
        source="seedlab",
    )
    assert isinstance(params, BacktestParams)
    return params


def _log(run_id: str, msg: str) -> None:
    """Append a timestamped line to the run's log buffer."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    buf = _logs[run_id]
    buf.append(line)
    if len(buf) > _MAX_LOG_LINES:
        buf[:] = buf[-_MAX_LOG_LINES:]
    logger.info("[seedlab:%s] %s", run_id, msg)


def get_logs(run_id: str, offset: int = 0) -> list[str]:
    return _logs.get(run_id, [])[offset:]


def is_running(run_id: str) -> bool:
    t = _tasks.get(run_id)
    return t is not None and not t.done()


def request_stop(run_id: str) -> bool:
    if run_id in _tasks and not _tasks[run_id].done():
        _stop_flags[run_id] = True
        return True
    return False


def delete_run_data(run_id: str) -> None:
    _logs.pop(run_id, None)
    _stop_flags.pop(run_id, None)
    t = _tasks.pop(run_id, None)
    if t and not t.done():
        t.cancel()


async def start_seedlab_run(
    run_id: str,
    symbol: str,
    days: int,
    balance: float,
    session_factory: async_sessionmaker | None = None,
    use_combinatorial: bool = False,
    max_combinatorial_seeds: int = 30,
) -> None:
    """Spawn a background task to run the SeedLab pipeline."""
    if run_id in _tasks and not _tasks[run_id].done():
        return

    _stop_flags[run_id] = False
    _logs[run_id] = []

    task = asyncio.create_task(
        _run_seedlab(
            run_id, symbol, days, balance, session_factory,
            use_combinatorial, max_combinatorial_seeds,
        )
    )
    _tasks[run_id] = task


async def _run_seedlab(
    run_id: str,
    symbol: str,
    days: int,
    balance: float,
    session_factory: async_sessionmaker | None,
    use_combinatorial: bool,
    max_combinatorial_seeds: int,
) -> None:
    """Background coroutine that runs the SeedLab pipeline."""
    _log(run_id, f"Starting SeedLab: {symbol}, {days}d, ${balance:.0f}")

    try:
        # Fetch data for regime detection and backtesting
        from alphaloop.backtester.runner import _fetch_data
        _log(run_id, "Fetching historical data...")
        opens, highs, lows, closes, timestamps = await _fetch_data(
            symbol, days, run_id, "1h"
        )
        _log(run_id, f"Loaded {len(closes)} bars")

        # Configure SeedLab
        from alphaloop.seedlab.runner import SeedLabConfig, SeedLabRunner
        from alphaloop.seedlab.regime_runner import RegimeRunner
        from alphaloop.seedlab.registry import CardRegistry

        config = SeedLabConfig(
            symbol=symbol,
            days=days,
            balance=balance,
            use_template_seeds=True,
            use_combinatorial_seeds=use_combinatorial,
            max_combinatorial_seeds=max_combinatorial_seeds,
        )

        # Create a simple backtest function for the pipeline
        from alphaloop.backtester.engine import BacktestEngine
        from alphaloop.backtester.runner import make_signal_fn
        engine = BacktestEngine(session_factory=session_factory)

        async def backtest_fn(
            filters: list[str],
            start_idx: int = 0,
            end_idx: int | None = None,
            bt_opens=opens, bt_highs=highs, bt_lows=lows,
            bt_closes=closes, bt_timestamps=timestamps,
            **_kwargs,
        ):
            # Slice data to regime segment if start_idx/end_idx provided
            s = start_idx
            e = (end_idx + 1) if end_idx is not None else len(bt_closes)
            seg_opens = bt_opens[s:e]
            seg_highs = bt_highs[s:e]
            seg_lows = bt_lows[s:e]
            seg_closes = bt_closes[s:e]
            seg_ts = bt_timestamps[s:e]
            params = _seedlab_backtest_params(filters)
            sig_fn = make_signal_fn(params, filters)
            return await engine.run(
                symbol=symbol,
                opens=seg_opens, highs=seg_highs, lows=seg_lows,
                closes=seg_closes, timestamps=seg_ts,
                balance=balance, risk_pct=params.risk_pct,
                filters=filters, signal_fn=sig_fn,
                stop_check=lambda: _stop_flags.get(run_id, False),
            )

        runner = SeedLabRunner(
            regime_runner=RegimeRunner(),
            registry=CardRegistry(),
        )

        def progress_cb(phase: str, current: int, total: int) -> None:
            _log(run_id, f"[{phase}] {current}/{total}")

        result = await runner.run(
            config=config,
            backtest_fn=backtest_fn,
            highs=highs,
            lows=lows,
            closes=closes,
            run_id=run_id,
            stop_check=lambda: _stop_flags.get(run_id, False),
            progress_callback=progress_cb,
        )

        _log(run_id, "=" * 50)
        if result.success:
            _log(run_id, f"SeedLab completed: {result.evaluated_seeds} evaluated, "
                         f"{result.passed_seeds} passed, {result.rejected_seeds} rejected")
            if result.ranked_scores:
                top = result.ranked_scores[0]
                _log(run_id, f"Top seed: {top.seed_name} (score={top.total_score:.3f})")
            _log(run_id, f"Elapsed: {result.elapsed_seconds:.1f}s")
        else:
            _log(run_id, f"SeedLab failed: {result.error}")

    except Exception as exc:
        _log(run_id, f"FATAL: {exc}")
        logger.exception("SeedLab %s failed", run_id)
