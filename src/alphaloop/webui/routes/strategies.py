"""
GET/POST /api/strategies — Strategy version management & promotion.

Serves the strategy lifecycle:
  List versions -> Evaluate promotion -> Promote -> Activate for live
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.webui.deps import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

STRATEGY_VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "strategy_versions"


class PromoteRequest(BaseModel):
    cycles_completed: int = 0


class CanaryRequest(BaseModel):
    allocation_pct: float = 10.0
    duration_hours: int = 24


def _load_all_versions() -> list[dict]:
    """Load all strategy version JSONs."""
    if not STRATEGY_VERSIONS_DIR.exists():
        return []
    versions = []
    for f in sorted(STRATEGY_VERSIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            data["_path"] = str(f)
            versions.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return versions


def _load_version(symbol: str, version: int) -> dict | None:
    """Load a specific strategy version."""
    path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        data["_path"] = str(path)
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_version(data: dict) -> None:
    """Save a strategy version back to disk."""
    path = Path(data.get("_path", ""))
    if not path.exists():
        symbol = data["symbol"]
        version = data["version"]
        path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"

    # Remove internal fields before saving
    save_data = {k: v for k, v in data.items() if not k.startswith("_")}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(save_data, indent=2))
    tmp.replace(path)


@router.get("")
async def list_strategies(
    symbol: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """List all strategy versions, optionally filtered by symbol/status."""
    versions = _load_all_versions()
    if symbol:
        versions = [v for v in versions if v.get("symbol") == symbol]
    if status:
        versions = [v for v in versions if v.get("status") == status]
    return {"strategies": versions[:limit], "total": len(versions)}


@router.get("/{symbol}/v{version}")
async def get_strategy(symbol: str, version: int) -> dict:
    """Get a specific strategy version."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")
    return data


@router.post("/{symbol}/v{version}/evaluate")
async def evaluate_promotion(
    symbol: str,
    version: int,
    body: PromoteRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Evaluate whether a strategy is eligible for promotion."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
    from alphaloop.core.config import EvolutionConfig
    from alphaloop.core.events import EventBus
    from alphaloop.core.types import StrategyStatus
    from alphaloop.webui.deps import _get_session_factory

    sf = _get_session_factory()
    if sf is None:
        raise HTTPException(500, "Session factory unavailable")

    pipeline = DeploymentPipeline(
        session_factory=sf,
        event_bus=EventBus(),
        evolution_config=EvolutionConfig(),
    )

    current_status = StrategyStatus(data.get("status", "candidate"))
    metrics = data.get("summary", {})

    result = await pipeline.evaluate_promotion(
        current_status=current_status,
        metrics=metrics,
        cycles_completed=body.cycles_completed,
    )

    return {
        "symbol": symbol,
        "version": version,
        "current_status": current_status,
        **result,
    }


@router.post("/{symbol}/v{version}/promote")
async def promote_strategy(
    symbol: str,
    version: int,
    body: PromoteRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Promote a strategy to the next deployment stage."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
    from alphaloop.core.config import EvolutionConfig
    from alphaloop.core.events import EventBus
    from alphaloop.core.types import StrategyStatus
    from alphaloop.webui.deps import _get_session_factory

    sf = _get_session_factory()
    if sf is None:
        raise HTTPException(500, "Session factory unavailable")

    pipeline = DeploymentPipeline(
        session_factory=sf,
        event_bus=EventBus(),
        evolution_config=EvolutionConfig(),
    )

    current_status = StrategyStatus(data.get("status", "candidate"))
    metrics = data.get("summary", {})

    result = await pipeline.promote(
        symbol=symbol,
        strategy_version=f"v{version}",
        current_status=current_status,
        metrics=metrics,
        cycles_completed=body.cycles_completed,
    )

    if result["promoted"]:
        # Update the version file with new status
        data["status"] = result["new_status"]
        _save_version(data)
        logger.info(
            "Strategy %s v%d promoted: %s -> %s",
            symbol, version, current_status, result["new_status"],
        )

    return {
        "symbol": symbol,
        "version": version,
        **result,
    }


@router.post("/{symbol}/v{version}/activate")
async def activate_strategy(
    symbol: str,
    version: int,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Set a strategy as the active live strategy for its symbol."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    status = data.get("status", "candidate")
    if status not in ("live", "demo", "dry_run"):
        raise HTTPException(
            400,
            f"Cannot activate strategy with status '{status}'. "
            f"Must be at least 'dry_run'. Promote first.",
        )

    # Save active strategy reference in DB settings
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)
    await repo.set(f"active_strategy_{symbol}", json.dumps({
        "symbol": symbol,
        "version": version,
        "status": status,
        "params": data.get("params", {}),
        "tools": data.get("tools", {}),
        "validation": data.get("validation", {}),
        "ai_models": data.get("ai_models", {}),
        "signal_mode": data.get("signal_mode", "algo_plus_ai"),
    }))
    await session.commit()

    logger.info("Activated strategy %s v%d for live trading", symbol, version)
    return {
        "status": "ok",
        "activated": f"{symbol} v{version}",
        "strategy_status": status,
    }


@router.post("/{symbol}/v{version}/canary/start")
async def start_canary(
    symbol: str,
    version: int,
    body: CanaryRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start a canary deployment for a strategy version."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
    from alphaloop.core.config import EvolutionConfig
    from alphaloop.core.events import EventBus
    from alphaloop.webui.deps import _get_session_factory

    sf = _get_session_factory()
    if sf is None:
        raise HTTPException(500, "Session factory unavailable")

    pipeline = DeploymentPipeline(
        session_factory=sf,
        event_bus=EventBus(),
        evolution_config=EvolutionConfig(),
    )

    result = await pipeline.start_canary(
        symbol=symbol,
        strategy_version=f"v{version}",
        allocation_pct=body.allocation_pct,
        duration_hours=body.duration_hours,
    )

    return result


@router.post("/{symbol}/v{version}/canary/end")
async def end_canary(
    symbol: str,
    version: int,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """End a canary deployment and get recommendation."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    from alphaloop.backtester.deployment_pipeline import DeploymentPipeline
    from alphaloop.core.config import EvolutionConfig
    from alphaloop.core.events import EventBus
    from alphaloop.webui.deps import _get_session_factory

    sf = _get_session_factory()
    if sf is None:
        raise HTTPException(500, "Session factory unavailable")

    pipeline = DeploymentPipeline(
        session_factory=sf,
        event_bus=EventBus(),
        evolution_config=EvolutionConfig(),
    )

    # Use summary metrics from the version as placeholder
    # In production, this would pull live canary trade metrics from DB
    metrics = data.get("summary", {})

    result = await pipeline.end_canary(
        symbol=symbol,
        strategy_version=f"v{version}",
        canary_id=f"canary_{symbol}_{version}",
        metrics=metrics,
    )

    return result


@router.put("/{symbol}/v{version}/models")
async def update_strategy_models(
    symbol: str,
    version: int,
    body: dict,
) -> dict:
    """Update AI model assignments for a strategy version."""
    path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"
    if not path.exists():
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    data = json.loads(path.read_text())
    if "ai_models" not in data:
        data["ai_models"] = {}

    for role in ["signal", "validator", "research", "autolearn", "fallback"]:
        if role in body:
            data["ai_models"][role] = body[role]

    # Atomic write
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)

    logger.info("Updated AI models for %s v%d: %s", symbol, version, data["ai_models"])
    return {"status": "ok", "ai_models": data["ai_models"]}


@router.get("/{symbol}/v{version}/overlay")
async def get_overlay(
    symbol: str,
    version: int,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get dry-run overlay config for a strategy version."""
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)
    raw = await repo.get(f"dry_run_overlay_{symbol}_v{version}")
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {"extra_tools": []}


@router.put("/{symbol}/v{version}/overlay")
async def set_overlay(
    symbol: str,
    version: int,
    body: dict,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Set dry-run overlay tools for a strategy version."""
    extra_tools = body.get("extra_tools", [])
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)
    await repo.set(
        f"dry_run_overlay_{symbol}_v{version}",
        json.dumps({"extra_tools": extra_tools}),
    )
    await session.commit()
    logger.info("Set overlay for %s v%d: %s", symbol, version, extra_tools)
    return {"status": "ok", "extra_tools": extra_tools}


@router.delete("/{symbol}/v{version}")
async def delete_strategy(
    symbol: str,
    version: int,
) -> dict:
    """Delete a strategy version JSON file."""
    path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"
    if not path.exists():
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    path.unlink()
    logger.info("Deleted strategy %s v%d", symbol, version)
    return {"status": "ok", "deleted": f"{symbol}_v{version}"}
