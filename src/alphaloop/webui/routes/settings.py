"""GET/PUT /api/settings — read/write app settings from DB."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.core.constants import RISK_HARD_CAPS
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.webui.deps import get_db_session
from alphaloop.webui.auth_rbac import Role, require_role

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    settings: dict[str, str]


import re as _re

# Phase 7D: patterns that indicate secret values — mask on read
_SECRET_PATTERNS = _re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", _re.IGNORECASE
)

_RISK_SETTING_TO_CAP_KEY = {
    "RISK_PCT": "risk_per_trade_pct",
    "MAX_DAILY_LOSS_PCT": "max_daily_loss_pct",
    "MARGIN_CAP_PCT": "margin_cap_pct",
    "MAX_PORTFOLIO_HEAT_PCT": "max_portfolio_heat_pct",
    "RISK_SCORE_THRESHOLD": "risk_score_threshold",
    "MAX_SESSION_LOSS_PCT": "max_session_loss_pct",
    "CONSECUTIVE_LOSS_LIMIT": "consecutive_loss_limit",
}

_INT_SETTINGS = {
    "MAX_CONCURRENT_TRADES": (1, 20),
    "LEVERAGE": (1, 2000),
    "CONTRACT_SIZE": (1, 10_000_000),
}


def _mask_secrets(settings: dict[str, str]) -> dict[str, str]:
    """Mask values of keys matching secret patterns."""
    masked = {}
    for k, v in settings.items():
        if _SECRET_PATTERNS.search(k) and v:
            masked[k] = f"***...{v[-4:]}" if len(v) > 4 else "***"
        else:
            masked[k] = v
    return masked


def _validate_settings_update(settings: dict[str, str]) -> None:
    """Reject unsafe or malformed live risk writes before they hit the DB."""
    for key, raw_value in settings.items():
        if key in _RISK_SETTING_TO_CAP_KEY:
            cap_key = _RISK_SETTING_TO_CAP_KEY[key]
            lo, hi, _default = RISK_HARD_CAPS[cap_key]
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} must be numeric",
                ) from exc
            if value < lo or value > hi:
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} must be between {lo} and {hi}",
                )

        if key in _INT_SETTINGS:
            lo, hi = _INT_SETTINGS[key]
            try:
                value = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} must be an integer",
                ) from exc
            if value < lo or value > hi:
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} must be between {lo} and {hi}",
                )


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for settings writes when AUTH_TOKEN is configured."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("")
async def get_settings(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all app settings (secrets masked)."""
    repo = SettingsRepository(session)
    settings = await repo.get_all()
    return {"settings": _mask_secrets(settings)}


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    request: Request = None,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
    _rbac: None = require_role(Role.ADMIN),
) -> dict:
    """Update multiple settings at once."""
    _require_operator_auth(authorization)
    repo = SettingsRepository(session)

    # Phase 7L: Get old values for audit trail
    old_settings = await repo.get_all()
    _validate_settings_update(body.settings)

    await repo.set_many(body.settings)

    # Phase 7L: Write operator audit records
    try:
        from alphaloop.db.models.operator_audit import OperatorAuditLog
        _ip = request.client.host if request and request.client else "unknown"
        for key, new_val in body.settings.items():
            old_val = old_settings.get(key)
            session.add(OperatorAuditLog(
                operator="webui",
                action="settings_update",
                target=key,
                old_value=str(old_val) if old_val else None,
                new_value=str(new_val) if not _SECRET_PATTERNS.search(key) else "***",
                source_ip=_ip,
            ))
        await session.flush()
    except Exception as exc:
        # Keep settings writes non-blocking, but do not hide audit failures.
        import logging
        logging.getLogger(__name__).warning(
            "Operator audit logging failed during settings update: %s",
            exc,
        )

    return {"status": "ok", "updated": list(body.settings.keys())}


@router.get("/usage")
async def get_api_usage() -> dict:
    """Return per-provider session usage stats from the in-memory performance tracker."""
    from alphaloop.ai.performance import model_performance_tracker as pt
    from alphaloop.ai.model_hub import get_model_by_id

    summary = pt.get_summary()
    by_provider: dict[str, dict] = {}

    for model_id, stats in summary.get("models", {}).items():
        cfg = get_model_by_id(model_id)
        if cfg is None:
            continue
        provider = cfg.provider.value  # e.g. "gemini", "anthropic"
        if provider not in by_provider:
            by_provider[provider] = {"calls": 0, "errors": 0, "models": []}
        calls = stats["call_count"]
        errors = round(stats["error_rate"] * calls)
        by_provider[provider]["calls"] += calls
        by_provider[provider]["errors"] += errors
        by_provider[provider]["models"].append({
            "id": model_id,
            "calls": calls,
            "error_rate": stats["error_rate"],
            "avg_latency_ms": stats["avg_latency_ms"],
        })

    return {"usage": by_provider, "total_calls": summary.get("total_calls", 0)}
