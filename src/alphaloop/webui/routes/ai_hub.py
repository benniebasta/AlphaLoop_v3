"""GET/PUT /api/ai-hub — model catalog, role assignments."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.core.config import AppConfig
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.webui.deps import get_config, get_db_session

router = APIRouter(prefix="/api/ai-hub", tags=["ai-hub"])

# Known AI model roles — keys match frontend ROLE_KEYS
_MODEL_ROLES = {
    "default_signal_model": "default_signal_model",
    "default_validator_model": "default_validator_model",
    "default_research_model": "default_research_model",
    "default_autolearn_model": "default_autolearn_model",
    # Legacy v2 keys (kept for migration compat)
    "signal_provider": "signal_provider",
    "signal_model": "signal_model",
    "claude_model": "claude_model",
    "claude_enabled": "claude_enabled",
    "qwen_signal_model": "qwen_signal_model",
    "qwen_validator_model": "qwen_validator_model",
    "qwen_validator_enabled": "qwen_validator_enabled",
}


class AIHubUpdate(BaseModel):
    settings: dict[str, str]


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for AI hub writes when AUTH_TOKEN is configured."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("")
async def get_ai_hub(
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return AI model catalog and current role assignments."""
    repo = SettingsRepository(session)
    db_settings = await repo.get_all()

    # Merge config defaults with DB overrides
    models = {}
    api_cfg = config.api
    for role, key in _MODEL_ROLES.items():
        db_val = db_settings.get(key)
        default_val = str(getattr(api_cfg, key, ""))
        models[role] = db_val if db_val else default_val

    return {
        "models": models,
        "providers": ["gemini", "openai", "claude", "deepseek", "qwen", "xai"],
        "api_keys_configured": {
            "gemini": bool(api_cfg.gemini_api_key.get_secret_value()),
            "openai": bool(api_cfg.openai_api_key.get_secret_value()),
            "claude": bool(api_cfg.claude_api_key.get_secret_value()),
            "deepseek": bool(api_cfg.deepseek_api_key.get_secret_value()),
            "qwen": bool(api_cfg.qwen_api_key.get_secret_value()),
            "xai": bool(api_cfg.xai_api_key.get_secret_value()),
        },
    }


@router.put("")
async def update_ai_hub(
    body: AIHubUpdate,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update AI model role assignments."""
    _require_operator_auth(authorization)
    repo = SettingsRepository(session)
    old_settings = await repo.get_all()
    await repo.set_many(body.settings)
    source_ip = request.client.host if request and request.client else "unknown"
    for key, new_val in body.settings.items():
        session.add(OperatorAuditLog(
            operator="webui",
            action="ai_hub_update",
            target=key,
            old_value=old_settings.get(key),
            new_value=json.dumps({"value": new_val}),
            source_ip=source_ip,
        ))
    await session.commit()
    return {"status": "ok", "updated": list(body.settings.keys())}


@router.get("/performance")
async def get_model_performance() -> dict:
    """
    Per-model AI performance metrics — latency, error rate, call count.
    Data is in-memory and resets on process restart.
    """
    try:
        from alphaloop.ai.performance import model_performance_tracker
        return model_performance_tracker.get_summary()
    except Exception as e:
        return {"models": {}, "worst_model": None, "total_calls": 0, "error": str(e)}


@router.get("/calibration")
async def get_calibration() -> dict:
    """
    AI validator calibration — Expected Calibration Error (ECE) and calibration curve.

    ECE measures how well the validator's risk_score matches actual approval outcomes.
    ECE < 0.10 = well calibrated; ECE > 0.10 = drift detected.
    """
    return {
        "ece": None,
        "n_samples": 0,
        "calibration_curve": [],
        "well_calibrated": None,
        "note": "ECE calibration removed (v3 validator path deleted)",
    }
