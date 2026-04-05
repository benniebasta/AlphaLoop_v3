"""Async repository for backtest run management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.backtest import BacktestRun


class BacktestRepository:
    _UPDATABLE_FIELDS = frozenset({
        "state", "generation", "phase", "message", "heartbeat_at",
        "best_fitness", "best_params", "result_json", "error",
        "started_at", "finished_at", "updated_at",
        "best_sharpe", "best_wr", "best_pnl", "best_dd", "best_trades",
        "error_message", "checkpoint_path", "pid",
    })

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> BacktestRun:
        run = BacktestRun(**kwargs)
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_by_run_id(self, run_id: str) -> BacktestRun | None:
        result = await self._session.execute(
            select(BacktestRun).where(BacktestRun.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_active_runs(self) -> list[BacktestRun]:
        result = await self._session.execute(
            select(BacktestRun)
            .where(BacktestRun.state.in_(["pending", "running", "paused"]))
            .order_by(BacktestRun.created_at.desc())
        )
        return list(result.scalars())

    async def get_runs(
        self,
        symbol: str | None = None,
        state: str | None = None,
        limit: int = 50,
    ) -> list[BacktestRun]:
        q = select(BacktestRun)
        if symbol:
            q = q.where(BacktestRun.symbol == symbol)
        if state:
            q = q.where(BacktestRun.state == state)
        q = q.order_by(BacktestRun.created_at.desc()).limit(limit)
        result = await self._session.execute(q)
        return list(result.scalars())

    async def update_state(
        self, run_id: str, state: str, **kwargs: Any
    ) -> BacktestRun | None:
        run = await self.get_by_run_id(run_id)
        if run is None:
            return None
        run.state = state
        for key, value in kwargs.items():
            if key in self._UPDATABLE_FIELDS and hasattr(run, key):
                setattr(run, key, value)
        run.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return run

    async def update_progress(
        self,
        run_id: str,
        generation: int,
        phase: str | None = None,
        message: str | None = None,
        **best: Any,
    ) -> None:
        run = await self.get_by_run_id(run_id)
        if run is None:
            return
        run.generation = generation
        run.heartbeat_at = datetime.now(timezone.utc)
        if phase:
            run.phase = phase
        if message:
            run.message = message
        for key, value in best.items():
            if key in self._UPDATABLE_FIELDS and hasattr(run, key):
                setattr(run, key, value)
        await self._session.flush()
