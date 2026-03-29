"""GET/POST/PATCH/DELETE /api/backtests — backtest run management."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.backtester import runner as bt_runner
from alphaloop.db.repositories.backtest_repo import BacktestRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

# ── Creative name generator ──────────────────────────────────────────────────

_ADJECTIVES = [
    "alpha", "blazing", "cosmic", "dark", "electric", "fierce", "golden",
    "hyper", "iron", "jade", "kinetic", "lunar", "mystic", "nova", "omega",
    "phantom", "quantum", "rapid", "shadow", "turbo", "ultra", "vortex",
    "wild", "xenon", "zen", "atomic", "binary", "cyber", "delta", "echo",
    "flash", "ghost", "hawk", "ice", "jet", "krypton", "laser", "matrix",
    "neon", "orbit", "pulse", "rogue", "sonic", "titan", "volt", "warp",
]

_NOUNS = [
    "archer", "bolt", "cobra", "dagger", "eagle", "falcon", "griffin",
    "hunter", "impulse", "javelin", "knight", "lion", "mantis", "nexus",
    "oracle", "panther", "quasar", "raptor", "serpent", "thunder", "viper",
    "wolf", "blade", "comet", "drift", "forge", "glacier", "hornet",
    "inferno", "kraken", "leopard", "meteor", "nova", "onyx", "phoenix",
    "raven", "storm", "trident", "wraith", "zenith", "blaze", "claw",
]




# ── Symbol catalog (served once, cached by frontend) ────────────────────────

@router.get("/symbols")
async def list_symbols() -> dict:
    """Return all yfinance-compatible symbols grouped by asset class."""
    from alphaloop.data.yf_catalog import get_catalog_for_api, CATALOG
    return {
        "symbols": get_catalog_for_api(),
        "groups": list(CATALOG.keys()),
    }


class BacktestCreate(BaseModel):
    symbol: str = "XAUUSD"
    name: str | None = None
    days: int = 365
    balance: float = 10000.0
    max_generations: int = 10
    timeframe: str = "15m"  # 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo
    # Backtest-compatible tool toggles
    use_session_filter: bool = True
    use_volatility_filter: bool = True
    use_ema200_filter: bool = True
    use_bos_guard: bool = False
    use_fvg_guard: bool = False
    use_tick_jump_guard: bool = False
    use_liq_vacuum_guard: bool = False
    use_vwap_guard: bool = False
    use_macd_filter: bool = False
    use_bollinger_filter: bool = False
    use_adx_filter: bool = False
    use_volume_filter: bool = False
    use_swing_structure: bool = False
    signal_mode: str | None = None  # "ai" or "algorithmic" — frontend selector


def _run_to_dict(r) -> dict:
    return {
        "id": r.id,
        "run_id": r.run_id,
        "symbol": r.symbol,
        "name": r.name,
        "state": r.state,
        "days": r.days,
        "timeframe": getattr(r, "timeframe", "1h"),
        "balance": r.balance,
        "max_generations": r.max_generations,
        "generation": r.generation,
        "phase": r.phase,
        "message": r.message,
        "best_sharpe": r.best_sharpe,
        "best_wr": r.best_wr,
        "best_pnl": r.best_pnl,
        "best_dd": r.best_dd,
        "best_trades": r.best_trades,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "error_message": r.error_message,
        "is_running": bt_runner.is_running(r.run_id),
    }


@router.get("")
async def list_backtests(
    state: str | None = Query(None),
    symbol: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    repo = BacktestRepository(session)
    runs = await repo.get_runs(symbol=symbol, state=state, limit=limit)
    return {"backtests": [_run_to_dict(r) for r in runs]}


@router.get("/{run_id}")
async def get_backtest(
    run_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    repo = BacktestRepository(session)
    run = await repo.get_by_run_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return _run_to_dict(run)


@router.post("")
async def create_backtest(
    body: BacktestCreate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create and immediately start a backtest run."""
    repo = BacktestRepository(session)

    # Auto-generate creative name if not provided
    # Always v1 — versioning happens at strategy card level (auto-learn + mutate)
    name = body.name
    if not name:
        adj = random.choice(_ADJECTIVES)
        noun = random.choice(_NOUNS)
        name = f"{adj}-{noun}-{body.symbol}_v1"

    tools = [k.replace("use_", "") for k, v in {
        "use_session_filter": body.use_session_filter,
        "use_volatility_filter": body.use_volatility_filter,
        "use_ema200_filter": body.use_ema200_filter,
        "use_bos_guard": body.use_bos_guard,
        "use_fvg_guard": body.use_fvg_guard,
        "use_tick_jump_guard": body.use_tick_jump_guard,
        "use_liq_vacuum_guard": body.use_liq_vacuum_guard,
        "use_vwap_guard": body.use_vwap_guard,
        "use_macd_filter": body.use_macd_filter,
        "use_bollinger_filter": body.use_bollinger_filter,
        "use_adx_filter": body.use_adx_filter,
        "use_volume_filter": body.use_volume_filter,
        "use_swing_structure": body.use_swing_structure,
    }.items() if v]
    run = await repo.create(
        run_id=uuid.uuid4().hex[:12],
        symbol=body.symbol,
        name=name,
        days=body.days,
        timeframe=body.timeframe,
        balance=body.balance,
        max_generations=body.max_generations,
        tools_json=tools,
        state="pending",
    )
    await session.commit()

    from alphaloop.webui.deps import _get_session_factory
    sf = _get_session_factory()

    if sf:
        await bt_runner.start_backtest(
            run_id=run.run_id,
            symbol=body.symbol,
            days=body.days,
            balance=body.balance,
            max_generations=body.max_generations,
            session_factory=sf,
            timeframe=body.timeframe,
            tools=tools,
            name=name,
        )

    return {"status": "ok", "backtest": _run_to_dict(run)}


@router.patch("/{run_id}/stop")
async def stop_backtest(
    run_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Request a running backtest to stop."""
    repo = BacktestRepository(session)
    run = await repo.get_by_run_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    if bt_runner.request_stop(run_id):
        return {"status": "ok", "message": "Stop requested"}
    # Task not running (lost on server restart) — fix stale DB state
    if run.state == "running":
        run.state = "paused"
        run.message = "Stopped (task lost on server restart)"
        await session.commit()
        return {"status": "ok", "message": "Stale state fixed to paused"}
    return {"status": "ok", "message": "Not running"}


@router.patch("/{run_id}/resume")
async def resume_backtest(
    run_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Resume a paused backtest from where it left off."""
    repo = BacktestRepository(session)
    run = await repo.get_by_run_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    # Auto-fix stale "running" state (task lost on server restart)
    if run.state == "running" and not bt_runner.is_running(run_id):
        run.state = "paused"
        run.message = "Stopped (task lost on server restart)"
        await session.commit()
    if run.state != "paused":
        raise HTTPException(status_code=400, detail=f"Cannot resume from state '{run.state}'")

    from alphaloop.webui.deps import _get_session_factory
    sf = _get_session_factory()
    if sf:
        tools = run.tools_json if isinstance(run.tools_json, list) else []
        await bt_runner.start_backtest(
            run_id=run.run_id,
            symbol=run.symbol,
            days=run.days,
            balance=run.balance,
            max_generations=run.max_generations,
            session_factory=sf,
            timeframe=run.timeframe or "1h",
            tools=tools,
            name=run.name or "",
        )
    return {"status": "ok", "message": "Resumed"}


@router.delete("/{run_id}")
async def delete_backtest(
    run_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a backtest run (stops if running)."""
    repo = BacktestRepository(session)
    run = await repo.get_by_run_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    bt_runner.delete_run_data(run_id)
    await session.delete(run)
    await session.commit()
    return {"status": "ok", "deleted": run_id}


@router.get("/{run_id}/logs")
async def get_logs(
    run_id: str,
    offset: int = Query(0, ge=0),
) -> dict:
    """Get log lines for a backtest run."""
    lines = bt_runner.get_logs(run_id, offset)
    return {"run_id": run_id, "offset": offset, "lines": lines, "total": offset + len(lines)}
