"""GET/PUT /api/ai-hub — model catalog, role assignments."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.core.config import AppConfig
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
            "gemini": bool(api_cfg.gemini_api_key),
            "openai": bool(api_cfg.openai_api_key),
            "claude": bool(api_cfg.claude_api_key),
            "deepseek": bool(api_cfg.deepseek_api_key),
            "qwen": bool(api_cfg.qwen_api_key),
            "xai": bool(api_cfg.xai_api_key),
        },
    }


@router.put("")
async def update_ai_hub(
    body: AIHubUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update AI model role assignments."""
    repo = SettingsRepository(session)
    await repo.set_many(body.settings)
    return {"status": "ok", "updated": list(body.settings.keys())}
