"""GET/POST/PATCH/DELETE /api/backtests — backtest run management."""

from __future__ import annotations

import json
import os
import random
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.backtester import runner as bt_runner
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.db.repositories.backtest_repo import BacktestRepository
from alphaloop.trading.strategy_loader import (
    build_strategy_resolution_input,
    normalize_strategy_signal_logic,
    normalize_strategy_signal_rules,
    normalize_strategy_tools,
    resolve_strategy_setup_family,
    resolve_strategy_signal_mode,
    resolve_strategy_source,
    serialize_strategy_spec,
)
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for backtest write actions when AUTH_TOKEN is configured."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

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
    signal_mode: str | None = None  # "algo_only" or "algo_ai"
    # Configurable signal sources
    signal_rules: list[dict] | None = None   # e.g. [{"source": "ema_crossover"}]
    signal_logic: str = "AND"                # "AND" | "OR" | "MAJORITY"
    signal_auto: bool = False                # let Optuna auto-pick sources


def _normalize_backtest_signal_mode(raw_mode: str | None) -> str:
    """SeedLab/backtests only emit algorithmic strategy families."""
    mode = (raw_mode or "algo_ai").strip().lower()
    if mode in {"algo_only", "algo_ai"}:
        return mode
    return "algo_ai"


def _parse_backtest_plan(plan: str | None) -> dict | None:
    """Return parsed plan payload when it is a JSON object."""
    if not plan:
        return None
    try:
        data = json.loads(plan)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _extract_backtest_signal_mode(plan: str | None) -> str:
    data = _parse_backtest_plan(plan)
    if data is not None:
        return _normalize_backtest_signal_mode(resolve_strategy_signal_mode(data))
    return _normalize_backtest_signal_mode(plan)


def _extract_backtest_setup_family(plan: str | None) -> str:
    data = _parse_backtest_plan(plan)
    if data is not None:
        return resolve_strategy_setup_family(data)
    return ""


def _extract_backtest_source(plan: str | None) -> str:
    data = _parse_backtest_plan(plan)
    if data is not None:
        return resolve_strategy_source(data)
    return ""


def _extract_backtest_strategy_spec(plan: str | None) -> dict:
    data = _parse_backtest_plan(plan)
    if data is not None:
        return serialize_strategy_spec(data)
    return {}


def _extract_backtest_tools(plan: str | None) -> list[str]:
    data = _parse_backtest_plan(plan)
    if data is None:
        return []
    flags = normalize_strategy_tools(data.get("tools") or {})
    return [name for name, enabled in flags.items() if enabled]


def _extract_backtest_signal_rules(plan: str | None) -> list[dict]:
    data = _parse_backtest_plan(plan)
    if data is not None:
        raw_rules = data.get("signal_rules")
        return normalize_strategy_signal_rules(
            raw_rules,
            default_to_ema=(raw_rules is None),
        )
    return normalize_strategy_signal_rules(None, default_to_ema=True)


def _extract_backtest_signal_logic(plan: str | None) -> str:
    data = _parse_backtest_plan(plan)
    if data is not None:
        return normalize_strategy_signal_logic(data.get("signal_logic"))
    return "AND"


def _build_backtest_plan_payload(
    *,
    signal_mode: str,
    signal_rules: list[dict],
    signal_logic: str,
    signal_auto: bool,
    tools: list[str],
) -> dict:
    tool_flags = {name: True for name in tools}
    strategy_like = build_strategy_resolution_input(
        {
            "signal_mode": signal_mode,
            "source": "backtest_runner",
            "tools": tool_flags,
        },
        signal_rules=signal_rules,
        signal_logic=signal_logic,
    )
    normalized_signal_rules = normalize_strategy_signal_rules(
        strategy_like["params"].get("signal_rules"),
        default_to_ema=(strategy_like["params"].get("signal_rules") is None),
    )
    normalized_signal_logic = normalize_strategy_signal_logic(
        strategy_like["params"].get("signal_logic")
    )
    strategy_like["params"]["signal_rules"] = normalized_signal_rules
    strategy_like["params"]["signal_logic"] = normalized_signal_logic
    return {
        "signal_mode": _normalize_backtest_signal_mode(resolve_strategy_signal_mode(strategy_like)),
        "setup_family": resolve_strategy_setup_family(strategy_like),
        "source": resolve_strategy_source(strategy_like),
        "strategy_spec": serialize_strategy_spec(strategy_like),
        "signal_rules": normalized_signal_rules,
        "signal_logic": normalized_signal_logic,
        "signal_auto": signal_auto,
        "tools": tool_flags,
    }


def _extract_plan_field(plan: str | None, field: str, default):
    """Extract a field from the plan JSON, falling back to default."""
    data = _parse_backtest_plan(plan)
    if data is not None:
        return data.get(field, default)
    return default


def _run_to_dict(r) -> dict:
    plan = getattr(r, "plan", None)
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
        "signal_mode": _extract_backtest_signal_mode(plan),
        "setup_family": _extract_backtest_setup_family(plan),
        "source": _extract_backtest_source(plan),
        "signal_rules": _extract_backtest_signal_rules(plan),
        "signal_logic": _extract_backtest_signal_logic(plan),
        "signal_auto": _extract_plan_field(plan, "signal_auto", False),
        "tools": _extract_backtest_tools(plan),
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
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create and immediately start a backtest run."""
    _require_operator_auth(authorization)
    repo = BacktestRepository(session)

    # Auto-generate creative name if not provided
    # Always v1 — versioning happens at strategy card level (auto-learn + mutate)
    name = body.name
    if not name:
        adj = random.choice(_ADJECTIVES)
        noun = random.choice(_NOUNS)
        name = f"{adj}-{noun}-{body.symbol}_v1"

    signal_mode = _normalize_backtest_signal_mode(body.signal_mode)

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
    signal_rules = normalize_strategy_signal_rules(
        body.signal_rules,
        default_to_ema=(body.signal_rules is None),
    )
    signal_logic = normalize_strategy_signal_logic(body.signal_logic)
    plan_payload = _build_backtest_plan_payload(
        signal_mode=signal_mode,
        signal_rules=signal_rules,
        signal_logic=signal_logic,
        signal_auto=body.signal_auto,
        tools=tools,
    )
    run = await repo.create(
        run_id=uuid.uuid4().hex[:12],
        symbol=body.symbol,
        name=name,
        plan=json.dumps(plan_payload),
        days=body.days,
        timeframe=body.timeframe,
        balance=body.balance,
        max_generations=body.max_generations,
        tools_json=plan_payload["tools"],
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
            signal_mode=signal_mode,
            signal_rules=signal_rules,
            signal_logic=signal_logic,
            signal_auto=body.signal_auto,
            setup_family=plan_payload["setup_family"],
            strategy_spec=plan_payload["strategy_spec"],
            source=plan_payload["source"],
        )

    session.add(OperatorAuditLog(
        operator="webui",
        action="backtest_create",
        target=run.run_id,
        old_value=None,
        new_value=json.dumps({
            "symbol": body.symbol,
            "days": body.days,
            "timeframe": body.timeframe,
            "signal_mode": plan_payload["signal_mode"],
            "setup_family": plan_payload["setup_family"],
            "source": plan_payload["source"],
        }, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    ))
    await session.commit()

    return {"status": "ok", "backtest": _run_to_dict(run)}


@router.patch("/{run_id}/stop")
async def stop_backtest(
    run_id: str,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Request a running backtest to stop."""
    _require_operator_auth(authorization)
    repo = BacktestRepository(session)
    run = await repo.get_by_run_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    if bt_runner.request_stop(run_id):
        session.add(OperatorAuditLog(
            operator="webui",
            action="backtest_stop",
            target=run_id,
            old_value=run.state,
            new_value="stop_requested",
            source_ip=request.client.host if request and request.client else "unknown",
        ))
        await session.commit()
        return {"status": "ok", "message": "Stop requested"}
    # Task not running (lost on server restart) — fix stale DB state
    if run.state == "running":
        run.state = "paused"
        run.message = "Stopped (task lost on server restart)"
        await session.commit()
        session.add(OperatorAuditLog(
            operator="webui",
            action="backtest_stop",
            target=run_id,
            old_value="running",
            new_value="paused",
            source_ip=request.client.host if request and request.client else "unknown",
        ))
        await session.commit()
        return {"status": "ok", "message": "Stale state fixed to paused"}
    return {"status": "ok", "message": "Not running"}


@router.patch("/{run_id}/resume")
async def resume_backtest(
    run_id: str,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Resume a paused backtest from where it left off."""
    _require_operator_auth(authorization)
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
        plan = getattr(run, "plan", None)
        await bt_runner.start_backtest(
            run_id=run.run_id,
            symbol=run.symbol,
            days=run.days,
            balance=run.balance,
            max_generations=run.max_generations,
            session_factory=sf,
            timeframe=run.timeframe or "1h",
            tools=_extract_backtest_tools(plan),
            name=run.name or "",
            signal_mode=_extract_backtest_signal_mode(plan),
            signal_rules=_extract_backtest_signal_rules(plan),
            signal_logic=_extract_backtest_signal_logic(plan),
            signal_auto=_extract_plan_field(plan, "signal_auto", False),
            setup_family=_extract_backtest_setup_family(plan),
            strategy_spec=_extract_backtest_strategy_spec(plan),
            source=_extract_backtest_source(plan),
        )
    session.add(OperatorAuditLog(
        operator="webui",
        action="backtest_resume",
        target=run_id,
        old_value="paused",
        new_value="running",
        source_ip=request.client.host if request and request.client else "unknown",
    ))
    await session.commit()
    return {"status": "ok", "message": "Resumed"}


@router.delete("/{run_id}")
async def delete_backtest(
    run_id: str,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a backtest run (stops if running)."""
    _require_operator_auth(authorization)
    repo = BacktestRepository(session)
    run = await repo.get_by_run_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Not found")
    bt_runner.delete_run_data(run_id)
    await session.delete(run)
    session.add(OperatorAuditLog(
        operator="webui",
        action="backtest_delete",
        target=run_id,
        old_value=run.state,
        new_value="deleted",
        source_ip=request.client.host if request and request.client else "unknown",
    ))
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
