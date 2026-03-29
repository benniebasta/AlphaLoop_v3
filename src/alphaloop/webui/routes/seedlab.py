"""GET/POST /api/seedlab — strategy discovery runs.

NOTE: This module is currently unused by the frontend. The backtests.js
UI hits /api/backtests instead. Kept for potential future use.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.seedlab import background_runner
from alphaloop.webui.deps import get_db_session, _get_session_factory

router = APIRouter(prefix="/api/seedlab", tags=["seedlab"])


class SeedLabRun(BaseModel):
    name: str
    symbol: str = "XAUUSD"
    days: int = 365
    balance: float = 10_000.0
    use_combinatorial: bool = False
    max_combinatorial_seeds: int = 30


@router.get("")
async def get_seedlab_runs(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return strategy discovery runs from settings store."""
    repo = SettingsRepository(session)
    import json

    raw = await repo.get("seedlab_runs", "[]")
    try:
        runs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        runs = []

    # Enrich with live task status
    for run in runs:
        rid = run.get("run_id")
        if rid:
            run["is_running"] = background_runner.is_running(rid)

    return {"runs": runs[:limit]}


@router.post("")
async def create_seedlab_run(
    body: SeedLabRun,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Queue and start a new strategy discovery run."""
    import json
    from datetime import datetime, timezone

    run_id = f"seedlab_{body.symbol}_{int(time.time())}"

    repo = SettingsRepository(session)
    raw = await repo.get("seedlab_runs", "[]")
    try:
        runs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        runs = []

    run_entry = {
        "run_id": run_id,
        "name": body.name,
        "symbol": body.symbol,
        "days": body.days,
        "balance": body.balance,
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    runs.insert(0, run_entry)
    await repo.set("seedlab_runs", json.dumps(runs[:100]))
    await session.commit()

    # Spawn background task
    sf = _get_session_factory()
    await background_runner.start_seedlab_run(
        run_id=run_id,
        symbol=body.symbol,
        days=body.days,
        balance=body.balance,
        session_factory=sf,
        use_combinatorial=body.use_combinatorial,
        max_combinatorial_seeds=body.max_combinatorial_seeds,
    )

    return {"status": "ok", "run_id": run_id, "run": run_entry}


@router.get("/{run_id}/logs")
async def get_seedlab_logs(
    run_id: str,
    offset: int = Query(0, ge=0),
) -> dict:
    """Stream logs from a running SeedLab run."""
    lines = background_runner.get_logs(run_id, offset)
    total = len(background_runner._logs.get(run_id, []))
    return {
        "run_id": run_id,
        "offset": offset,
        "lines": lines,
        "total": total,
        "is_running": background_runner.is_running(run_id),
    }


@router.patch("/{run_id}/stop")
async def stop_seedlab_run(run_id: str) -> dict:
    """Request a running SeedLab run to stop."""
    stopped = background_runner.request_stop(run_id)
    return {"status": "ok" if stopped else "not_running", "run_id": run_id}


@router.delete("/{run_id}")
async def delete_seedlab_run(
    run_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a SeedLab run (stop if running)."""
    background_runner.delete_run_data(run_id)

    # Remove from settings store
    import json
    repo = SettingsRepository(session)
    raw = await repo.get("seedlab_runs", "[]")
    try:
        runs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        runs = []
    runs = [r for r in runs if r.get("run_id") != run_id]
    await repo.set("seedlab_runs", json.dumps(runs))
    await session.commit()

    return {"status": "ok", "run_id": run_id}
