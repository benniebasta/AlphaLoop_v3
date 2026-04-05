"""GET /api/assets and PUT /api/assets/{symbol}/tools — per-asset filter presets."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.config.assets import ASSETS, AssetConfig
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/assets", tags=["assets"])

# Canonical tool keys — must stay in sync with SeedLab bt-tool-* checkbox IDs
# key → bt-tool-{key.replace("_", "-")}
_ALL_TOOLS = [
    "session", "volatility", "ema200",
    "bos", "fvg", "tick_jump", "liq_vacuum", "vwap",
    "macd", "bollinger", "adx", "volume", "swing",
]


def _default_tools(ac: AssetConfig) -> dict[str, bool]:
    """Derive sensible default tool presets from an AssetConfig."""
    is_crypto = ac.asset_class == "crypto"
    return {
        "session":     not is_crypto,   # crypto is 24/7 — session filter irrelevant
        "volatility":  True,
        "ema200":      True,
        "bos":         False,
        "fvg":         False,
        "tick_jump":   False,
        "liq_vacuum":  False,
        "vwap":        False,
        "macd":        False,
        "bollinger":   False,
        "adx":         False,
        "volume":      False,
        "swing":       False,
    }


@router.get("")
async def list_assets(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all configured assets with their current tool presets."""
    repo = SettingsRepository(session)
    result = []
    for symbol, ac in ASSETS.items():
        raw = await repo.get(f"ASSET_TOOLS_{symbol}", "")
        if raw:
            try:
                tools = json.loads(raw)
                # Fill any missing keys with defaults (new tools added later)
                defaults = _default_tools(ac)
                for k in _ALL_TOOLS:
                    if k not in tools:
                        tools[k] = defaults[k]
            except (json.JSONDecodeError, TypeError):
                tools = _default_tools(ac)
        else:
            tools = _default_tools(ac)

        result.append({
            "symbol":       symbol,
            "display_name": ac.display_name,
            "asset_class":  ac.asset_class,
            "tools":        tools,
        })
    return {"assets": result}


class ToolsUpdate(BaseModel):
    tools: dict[str, bool]


@router.put("/{symbol}/tools")
async def update_asset_tools(
    symbol: str,
    body: ToolsUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Save the filter/tool preset for a symbol."""
    symbol = symbol.upper()
    if symbol not in ASSETS:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not in asset library")
    repo = SettingsRepository(session)
    # Only persist recognised tool keys
    clean = {k: bool(v) for k, v in body.tools.items() if k in _ALL_TOOLS}
    await repo.set_many({f"ASSET_TOOLS_{symbol}": json.dumps(clean)})
    return {"status": "ok", "symbol": symbol, "tools": clean}
