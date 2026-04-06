"""
GET/POST /api/strategies — Strategy version management & promotion.

Serves the strategy lifecycle:
  List versions -> Evaluate promotion -> Promote -> Activate for live
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.backtester.asset_trainer import create_strategy_version
from alphaloop.backtester.params import BacktestParams
from alphaloop.core.constants import STRATEGY_VERSIONS_DIR
from alphaloop.db.models.instance import RunningInstance
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.trading.strategy_loader import (
    build_active_strategy_payload,
    normalize_signal_mode,
    normalize_strategy_summary,
    migrate_legacy_strategy_spec_v1,
    resolve_signal_instruction,
    resolve_strategy_signal_mode,
    resolve_strategy_spec_version,
    resolve_strategy_source,
    resolve_validator_instruction,
    serialize_strategy_spec,
)
from alphaloop.webui.deps import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

ALL_SIGNAL_MODES = {"algo_only", "algo_ai", "ai_signal"}
LEGACY_SIGNAL_MODES = {"algo_only", "algo_ai"}
DISCOVERY_SIGNAL_MODE = "ai_signal"
DISCOVERY_SOURCES = {"ai_signal_discovery", "ui_ai_signal_card"}
PROMOTION_GATE_KEYS = {
    "algo_only": "PROMOTION_CANDIDATE_GATE_ALGO_ONLY",
    "algo_ai": "PROMOTION_CANDIDATE_GATE_ALGO_AI",
    "ai_signal": "PROMOTION_CANDIDATE_GATE_AI_SIGNAL",
}
PROMOTION_GATE_DEFAULTS = {
    "algo_only": True,
    "algo_ai": True,
    "ai_signal": False,
}


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for strategy write actions when AUTH_TOKEN is set."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _record_operator_audit(
    session: AsyncSession,
    *,
    action: str,
    target: str,
    old_value: str | None,
    new_value: str | None,
    source_ip: str = "unknown",
) -> None:
    session.add(OperatorAuditLog(
        operator="webui",
        action=action,
        target=target,
        old_value=old_value,
        new_value=new_value,
        source_ip=source_ip,
    ))


class PromoteRequest(BaseModel):
    cycles_completed: int = 0


class CanaryRequest(BaseModel):
    allocation_pct: float = 10.0
    duration_hours: int = 24


class CreateAISignalCardRequest(BaseModel):
    symbol: str
    name: str | None = None
    signal_instruction: str | None = None
    validator_instruction: str | None = None
    source: str | None = None


class UpdateStrategyRequest(BaseModel):
    name: str | None = None
    status: str | None = None
    signal_mode: str | None = None
    source: str | None = None
    params: dict | None = None
    tools: dict | None = None
    validation: dict | None = None
    ai_models: dict | None = None
    signal_instruction: str | None = None
    validator_instruction: str | None = None
    scoring_weights: dict | None = None
    confidence_thresholds: dict | None = None


def _strict_signal_mode(raw_mode: str | None) -> str | None:
    mode = (raw_mode or "").strip().lower()
    return mode if mode in ALL_SIGNAL_MODES else None


def _parse_signal_mode_filter(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    modes: set[str] = set()
    for part in raw.split(","):
        mode = _strict_signal_mode(part)
        if mode:
            modes.add(mode)
    return modes or None


def _resolve_update_source(body_source: str | None, existing_source: str | None) -> str:
    return (body_source or existing_source or "").strip().lower()


def _validate_params(params: dict) -> None:
    """
    Validate strategy params against BacktestParams schema.
    Only validates known fields — unknown keys are passed through (plugin extensions).
    Raises HTTPException(422) on type/bounds violations.
    """
    known_fields = BacktestParams.model_fields
    errors: list[str] = []
    for key, val in params.items():
        if key not in known_fields:
            continue  # allow extension keys
        field_info = known_fields[key]
        # Coerce and validate type
        try:
            # Use pydantic to validate just this field
            BacktestParams(**{key: val})
        except Exception as exc:
            errors.append(f"params.{key}: {exc}")
    if errors:
        raise HTTPException(422, f"Invalid params: {'; '.join(errors)}")


_VALID_VALIDATION_LEVELS = {"strict", "standard", "algo_only"}


def _validate_validation_cfg(validation: dict) -> None:
    """Validate key thresholds in the validation config block."""
    errors: list[str] = []
    mc = validation.get("min_confidence")
    if mc is not None:
        try:
            mc = float(mc)
            if not (0.0 <= mc <= 1.0):
                errors.append(f"validation.min_confidence must be 0.0–1.0, got {mc}")
        except (ValueError, TypeError):
            errors.append(f"validation.min_confidence must be a float, got {mc!r}")
    rr = validation.get("min_rr")
    if rr is not None:
        try:
            rr = float(rr)
            if rr <= 0:
                errors.append(f"validation.min_rr must be > 0, got {rr}")
        except (ValueError, TypeError):
            errors.append(f"validation.min_rr must be a float, got {rr!r}")
    vl = validation.get("validation_level")
    if vl is not None:
        if str(vl).strip().lower() not in _VALID_VALIDATION_LEVELS:
            errors.append(
                f"validation.validation_level must be one of "
                f"{sorted(_VALID_VALIDATION_LEVELS)}, got {vl!r}"
            )
    if errors:
        raise HTTPException(422, f"Invalid validation config: {'; '.join(errors)}")


def _parse_bool_setting(raw: str | None, default: bool) -> bool:
    value = (raw or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _validate_signal_mode_for_source(signal_mode: str, source: str) -> None:
    if signal_mode not in ALL_SIGNAL_MODES:
        raise HTTPException(400, f"Unsupported signal_mode '{signal_mode}'")

    if signal_mode == DISCOVERY_SIGNAL_MODE and source not in DISCOVERY_SOURCES:
        raise HTTPException(
            400,
            "ai_signal is only allowed from AI Signal Discovery",
        )

    if signal_mode in LEGACY_SIGNAL_MODES and source in DISCOVERY_SOURCES:
        raise HTTPException(
            400,
            "AI Signal Discovery cards must stay in ai_signal mode",
        )


async def _candidate_gate_bypass(
    repo: SettingsRepository,
    source: str | None,
    signal_mode: str | None,
) -> bool:
    # All strategies go through the promotion gate regardless of source.
    # Removed previous bypass for ai_signal_discovery sources (H-12 audit fix).
    mode = normalize_signal_mode(signal_mode)
    key = PROMOTION_GATE_KEYS.get(mode)
    if key is None:
        return False
    default_enabled = PROMOTION_GATE_DEFAULTS.get(mode, False)
    enabled = _parse_bool_setting(
        await repo.get(key, str(default_enabled).lower()),
        default_enabled,
    )
    return not enabled


def _effective_signal_mode(data: dict) -> str:
    return resolve_strategy_signal_mode(data)


def _effective_source(data: dict) -> str:
    return resolve_strategy_source(data)


def _normalized_summary(data: dict) -> dict:
    return normalize_strategy_summary(data)


def _active_strategy_payload(data: dict) -> dict:
    payload = build_active_strategy_payload(data)
    payload["symbol"] = str(data.get("symbol") or payload.get("symbol") or "")
    return payload


def _refresh_strategy_spec(data: dict) -> None:
    data["strategy_spec"] = migrate_legacy_strategy_spec_v1(data).to_dict()


def _sync_strategy_spec_write_fields(data: dict, explicit_fields: set[str] | None = None) -> None:
    """
    Keep explicit strategy_spec in sync with intentional flat-field edits.

    The runtime prefers strategy_spec.prompt_bundle and strategy_spec.signal_mode,
    so write paths must update those fields when operators edit prompts or mode.
    """
    explicit_fields = explicit_fields or set()
    raw_spec = data.get("strategy_spec")
    if isinstance(raw_spec, dict):
        spec = dict(raw_spec)
        spec["signal_mode"] = (
            normalize_signal_mode(data.get("signal_mode"))
            if "signal_mode" in explicit_fields
            else _effective_signal_mode(data)
        )
        prompt_bundle = dict(spec.get("prompt_bundle") or {})
        prompt_bundle["signal_instruction"] = (
            str(data.get("signal_instruction") or "")
            if "signal_instruction" in explicit_fields
            else resolve_signal_instruction(data)
        )
        prompt_bundle["validator_instruction"] = (
            str(data.get("validator_instruction") or "")
            if "validator_instruction" in explicit_fields
            else resolve_validator_instruction(data)
        )
        spec["prompt_bundle"] = prompt_bundle
        metadata = dict(spec.get("metadata") or {})
        if "source" in explicit_fields:
            explicit_source = str(data.get("source") or "").strip()
            metadata["source"] = explicit_source or _effective_source(data)
        else:
            metadata["source"] = _effective_source(data)
        metadata["symbol"] = str(data.get("symbol") or "")
        metadata["version"] = int(data.get("version", 0) or 0)
        spec["metadata"] = metadata
        data["strategy_spec"] = spec

    data["strategy_spec"] = migrate_legacy_strategy_spec_v1(data).to_dict()


async def _sync_active_strategy_settings(
    session: AsyncSession,
    data: dict,
) -> None:
    """
    Keep active runtime settings aligned when a strategy file is edited.

    We only update runtime bindings that already point at the same
    symbol/version so unrelated active strategies stay untouched.
    """
    payload = json.dumps(_active_strategy_payload(data))
    symbol = data.get("symbol", "")
    version = data.get("version", 0)
    repo = SettingsRepository(session)

    async def _maybe_update(key: str) -> None:
        raw = await repo.get(key)
        if not raw:
            return
        try:
            current = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return
        if current.get("symbol") == symbol and int(current.get("version", -1)) == int(version):
            await repo.set(key, payload)

    await _maybe_update(f"active_strategy_{symbol}")

    result = await session.execute(
        select(RunningInstance.instance_id).where(RunningInstance.symbol == symbol)
    )
    for instance_id in result.scalars():
        await _maybe_update(f"active_strategy_{instance_id}")


def _load_all_versions() -> list[dict]:
    """Load all strategy version JSONs."""
    if not STRATEGY_VERSIONS_DIR.exists():
        return []
    versions = []
    for f in sorted(STRATEGY_VERSIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            _refresh_strategy_spec(data)
            data["signal_mode"] = _effective_signal_mode(data)
            data["summary"] = _normalized_summary(data)
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
        _refresh_strategy_spec(data)
        data["signal_mode"] = _effective_signal_mode(data)
        data["summary"] = _normalized_summary(data)
        data["_path"] = str(path)
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_version(data: dict, explicit_fields: set[str] | None = None) -> None:
    """Save a strategy version back to disk."""
    path = Path(data.get("_path", ""))
    if not path.name or not path.exists():
        symbol = data["symbol"]
        version = data["version"]
        path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"

    # Remove internal fields before saving
    explicit_fields = explicit_fields or set()
    save_data = {k: v for k, v in data.items() if not k.startswith("_")}
    if "signal_mode" not in explicit_fields:
        save_data["signal_mode"] = _effective_signal_mode(save_data)
    if "signal_instruction" not in explicit_fields:
        save_data["signal_instruction"] = resolve_signal_instruction(save_data)
    if "validator_instruction" not in explicit_fields:
        save_data["validator_instruction"] = resolve_validator_instruction(save_data)
    if "source" not in explicit_fields:
        save_data["source"] = _effective_source(save_data)
    if "summary" in save_data:
        save_data["summary"] = _normalized_summary(save_data)
    _sync_strategy_spec_write_fields(save_data, explicit_fields)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(save_data, indent=2))
    tmp.replace(path)


@router.get("")
async def list_strategies(
    symbol: str | None = Query(None),
    status: str | None = Query(None),
    signal_mode: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """List all strategy versions, optionally filtered by symbol/status."""
    versions = _load_all_versions()
    if symbol:
        versions = [v for v in versions if v.get("symbol") == symbol]
    if status:
        versions = [v for v in versions if v.get("status") == status]
    signal_modes = _parse_signal_mode_filter(signal_mode)
    if signal_mode and signal_modes is None:
        raise HTTPException(400, "Invalid signal_mode filter")
    if signal_modes:
        versions = [
            v for v in versions
            if _effective_signal_mode(v) in signal_modes
        ]
    return {"strategies": versions[:limit], "total": len(versions)}


@router.get("/{symbol}/v{version}")
async def get_strategy(symbol: str, version: int) -> dict:
    """Get a specific strategy version."""
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")
    return data


@router.put("/{symbol}/v{version}")
async def update_strategy(
    symbol: str,
    version: int,
    body: UpdateStrategyRequest,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update the editable parts of a strategy version."""
    _require_operator_auth(authorization)
    data = _load_version(symbol, version)
    if data is None:
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")
    explicit_fields = set(body.model_fields_set)

    if "signal_instruction" not in explicit_fields:
        data["signal_instruction"] = resolve_signal_instruction(data)
    if "validator_instruction" not in explicit_fields:
        data["validator_instruction"] = resolve_validator_instruction(data)

    if body.name is not None:
        data["name"] = body.name.strip()
    if body.status is not None:
        data["status"] = body.status.strip()
    if body.signal_mode is not None:
        mode = _strict_signal_mode(body.signal_mode)
        if mode is None:
            raise HTTPException(400, f"Unsupported signal_mode '{body.signal_mode}'")
        update_source = _resolve_update_source(body.source, _effective_source(data))
        _validate_signal_mode_for_source(mode, update_source)
        data["signal_mode"] = mode
    if body.source is not None:
        data["source"] = body.source.strip()
    if body.params is not None:
        _validate_params(body.params)
        data["params"] = body.params
    if body.tools is not None:
        # Validate all tool toggle values are booleans
        bad_tools = {k: v for k, v in body.tools.items() if not isinstance(v, bool)}
        if bad_tools:
            raise HTTPException(422, f"tools values must be booleans, got: {bad_tools}")
        data["tools"] = body.tools
    if body.validation is not None:
        _validate_validation_cfg(body.validation)
        data["validation"] = body.validation
    if body.ai_models is not None:
        data["ai_models"] = body.ai_models
    if body.signal_instruction is not None:
        data["signal_instruction"] = body.signal_instruction
    if body.validator_instruction is not None:
        data["validator_instruction"] = body.validator_instruction
    if body.scoring_weights is not None:
        data["scoring_weights"] = body.scoring_weights
    if body.confidence_thresholds is not None:
        data["confidence_thresholds"] = body.confidence_thresholds

    _save_version(data, explicit_fields)
    _record_operator_audit(
        session,
        action="strategy_update",
        target=f"{symbol}_v{version}",
        old_value="version_file",
        new_value=json.dumps({
            "signal_mode": _effective_signal_mode(data),
            "status": data.get("status"),
            "spec_version": resolve_strategy_spec_version(data) or "v1",
        }, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await _sync_active_strategy_settings(session, data)
    await session.commit()

    logger.info("Updated strategy %s v%d", symbol, version)
    return {
        "status": "ok",
        "strategy": _load_version(symbol, version) or data,
    }


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
    repo = SettingsRepository(session)
    bypass_candidate_gate = await _candidate_gate_bypass(
        repo,
        _effective_source(data),
        _effective_signal_mode(data),
    )

    result = await pipeline.evaluate_promotion(
        current_status=current_status,
        metrics=metrics,
        cycles_completed=body.cycles_completed,
        bypass_candidate_gate=bypass_candidate_gate,
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
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Promote a strategy to the next deployment stage."""
    _require_operator_auth(authorization)
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
    repo = SettingsRepository(session)
    bypass_candidate_gate = await _candidate_gate_bypass(
        repo,
        _effective_source(data),
        _effective_signal_mode(data),
    )

    result = await pipeline.promote(
        symbol=symbol,
        strategy_version=f"v{version}",
        current_status=current_status,
        metrics=metrics,
        cycles_completed=body.cycles_completed,
        bypass_candidate_gate=bypass_candidate_gate,
    )

    if result["promoted"]:
        # Update the version file with new status
        data["status"] = result["new_status"]
        _save_version(data)
        logger.info(
            "Strategy %s v%d promoted: %s -> %s",
            symbol, version, current_status, result["new_status"],
        )
    _record_operator_audit(
        session,
        action="strategy_promote",
        target=f"{symbol}_v{version}",
        old_value=str(current_status),
        new_value=json.dumps({
            "promoted": bool(result.get("promoted")),
            "new_status": result.get("new_status"),
            "reason": result.get("reason"),
        }, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()

    return {
        "symbol": symbol,
        "version": version,
        **result,
    }


@router.post("/ai-signal")
async def create_ai_signal_card(
    body: CreateAISignalCardRequest,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Create a new AI_SIGNAL strategy card with starter prompts."""
    _require_operator_auth(authorization)
    from alphaloop.backtester.params import BacktestParams

    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol is required")
    source = (body.source or "ai_signal_discovery").strip().lower()

    version_data = create_strategy_version(
        symbol=symbol,
        params=BacktestParams(
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            signal_rules=[],
            source=source,
            strategy_spec={
                "spec_version": "v1",
                "signal_mode": "ai_signal",
                "setup_family": "discretionary_ai",
                "entry_model": {
                    "type": "prompt_defined",
                    "signal_rules": [],
                    "signal_logic": "AND",
                },
                "metadata": {
                    "source": source,
                    "symbol": symbol,
                },
            },
        ),
        metrics={
            "total_trades": 0,
            "win_rate": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "total_pnl": 0.0,
        },
        tools=[
            "session_filter", "news_filter", "volatility_filter",
            "ema200_filter", "bos_guard", "fvg_guard",
            "tick_jump_guard", "liq_vacuum_guard", "vwap_guard",
            "dxy_filter", "sentiment_filter", "risk_filter",
            "correlation_guard",
        ],
        status="candidate",
        source=source,
        name=(body.name or "").strip(),
        signal_mode="ai_signal",
        signal_instruction=(body.signal_instruction or "").strip(),
        validator_instruction=(body.validator_instruction or "").strip(),
    )

    logger.info(
        "Created AI_SIGNAL card: %s v%d",
        version_data["symbol"],
        version_data["_version"],
    )
    _record_operator_audit(
        session,
        action="strategy_create",
        target=f"{version_data['symbol']}_v{version_data['_version']}",
        old_value=None,
        new_value=json.dumps({
            "signal_mode": version_data.get("signal_mode"),
            "source": resolve_strategy_source(version_data),
            "spec_version": resolve_strategy_spec_version(version_data) or "v1",
        }, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()
    return {
        "status": "ok",
        "strategy": version_data,
    }


@router.post("/{symbol}/v{version}/activate")
async def activate_strategy(
    symbol: str,
    version: int,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Set a strategy as the active live strategy for its symbol."""
    _require_operator_auth(authorization)
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
    await repo.set(f"active_strategy_{symbol}", json.dumps(
        _active_strategy_payload(data)
    ))
    _record_operator_audit(
        session,
        action="strategy_activate",
        target=f"{symbol}_v{version}",
        old_value=None,
        new_value=status,
        source_ip=request.client.host if request and request.client else "unknown",
    )
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
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Start a canary deployment for a strategy version."""
    _require_operator_auth(authorization)
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
    _record_operator_audit(
        session,
        action="strategy_canary_start",
        target=f"{symbol}_v{version}",
        old_value=None,
        new_value=json.dumps({
            "allocation_pct": body.allocation_pct,
            "duration_hours": body.duration_hours,
        }, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()

    return result


@router.post("/{symbol}/v{version}/canary/end")
async def end_canary(
    symbol: str,
    version: int,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """End a canary deployment and get recommendation."""
    _require_operator_auth(authorization)
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
    _record_operator_audit(
        session,
        action="strategy_canary_end",
        target=f"{symbol}_v{version}",
        old_value=None,
        new_value=json.dumps(result, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()

    return result


@router.put("/{symbol}/v{version}/models")
async def update_strategy_models(
    symbol: str,
    version: int,
    body: dict,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update AI model assignments for a strategy version."""
    _require_operator_auth(authorization)
    path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"
    if not path.exists():
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    data = json.loads(path.read_text())
    _refresh_strategy_spec(data)
    data["signal_mode"] = _effective_signal_mode(data)
    if "ai_models" not in data:
        data["ai_models"] = {}
    update_source = _resolve_update_source(body.get("source"), _effective_source(data))
    explicit_fields = set(body.keys())

    for role in ["signal", "validator", "research", "param_suggest", "regime", "fallback"]:
        if role in body:
            data["ai_models"][role] = body[role]

    if "signal_mode" in body:
        mode = _strict_signal_mode(body["signal_mode"])
        if mode is None:
            raise HTTPException(400, f"Unsupported signal_mode '{body['signal_mode']}'")
        _validate_signal_mode_for_source(mode, update_source)
        data["signal_mode"] = mode

    if "source" in body and body["source"] is not None:
        data["source"] = str(body["source"]).strip()

    if "signal_instruction" in body:
        data["signal_instruction"] = body["signal_instruction"] or ""
    if "validator_instruction" in body:
        data["validator_instruction"] = body["validator_instruction"] or ""

    _save_version(data, explicit_fields)
    data = _load_version(symbol, version) or data

    _record_operator_audit(
        session,
        action="strategy_models_update",
        target=f"{symbol}_v{version}",
        old_value="ai_models",
        new_value=json.dumps({
            "ai_models": data["ai_models"],
            "signal_mode": _effective_signal_mode(data),
        }, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await _sync_active_strategy_settings(session, data)
    await session.commit()

    logger.info("Updated AI models for %s v%d: %s", symbol, version, data["ai_models"])
    return {
        "status": "ok",
        "ai_models": data["ai_models"],
        "signal_mode": _effective_signal_mode(data),
        "signal_instruction": resolve_signal_instruction(data),
        "validator_instruction": resolve_validator_instruction(data),
    }


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
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Set dry-run overlay tools for a strategy version."""
    _require_operator_auth(authorization)
    extra_tools = body.get("extra_tools", [])
    from alphaloop.db.repositories.settings_repo import SettingsRepository
    repo = SettingsRepository(session)
    await repo.set(
        f"dry_run_overlay_{symbol}_v{version}",
        json.dumps({"extra_tools": extra_tools}),
    )
    _record_operator_audit(
        session,
        action="strategy_overlay_update",
        target=f"{symbol}_v{version}",
        old_value=None,
        new_value=json.dumps({"extra_tools": extra_tools}, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()
    logger.info("Set overlay for %s v%d: %s", symbol, version, extra_tools)
    return {"status": "ok", "extra_tools": extra_tools}


@router.delete("/{symbol}/v{version}")
async def delete_strategy(
    symbol: str,
    version: int,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete a strategy version JSON file."""
    _require_operator_auth(authorization)
    path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"
    if not path.exists():
        raise HTTPException(404, f"Strategy {symbol} v{version} not found")

    data = _load_version(symbol, version)
    if data and data.get("status") in ("live", "demo"):
        raise HTTPException(
            400,
            f"Cannot delete strategy with status '{data['status']}'. "
            f"Retire or demote it first.",
        )

    # Check if this version is the active strategy for any instance
    repo = SettingsRepository(session)
    active_raw = await repo.get(f"active_strategy_{symbol}")
    if active_raw:
        try:
            active = json.loads(active_raw)
            if int(active.get("version", -1)) == version:
                raise HTTPException(
                    400,
                    f"Strategy {symbol} v{version} is the active strategy. "
                    f"Activate a different version first.",
                )
        except (json.JSONDecodeError, TypeError):
            pass

    path.unlink()
    _record_operator_audit(
        session,
        action="strategy_delete",
        target=f"{symbol}_v{version}",
        old_value="version_file",
        new_value="deleted",
        source_ip=request.client.host if request and request.client else "unknown",
    )
    await session.commit()
    logger.info("Deleted strategy %s v%d", symbol, version)
    return {"status": "ok", "deleted": f"{symbol}_v{version}"}
