"""GET /api/assets and PUT /api/assets/{symbol}/tools — per-asset filter presets."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.config.assets import ASSETS, AssetConfig
from alphaloop.db.models.operator_audit import OperatorAuditLog
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/assets", tags=["assets"])

# Canonical tool keys — must stay in sync with SeedLab bt-tool-* checkbox IDs
# key → bt-tool-{key.replace("_", "-")}
_ALL_TOOLS = [
    # Core
    "session", "volatility", "news_filter", "risk_filter",
    # Structure
    "ema200", "bos", "fvg", "tick_jump", "liq_vacuum", "vwap",
    # Technical
    "macd", "bollinger", "adx", "volume", "swing",
    # Trend
    "ema_crossover", "alma_filter", "trendilo",
    # Momentum
    "rsi_feature", "fast_fingers", "choppiness_index",
    # Macro
    "dxy_filter", "sentiment_filter", "correlation_guard",
]


def _default_tools(ac: AssetConfig) -> dict[str, bool]:
    """Derive sensible default tool presets from an AssetConfig."""
    cls        = ac.asset_class
    is_crypto  = cls == "crypto"
    is_metal   = cls == "spot_metal"
    is_fx_maj  = cls == "forex_major"
    is_fx      = cls in ("forex_major", "forex_minor")
    is_index   = cls == "index"
    return {
        # Core
        "session":          not is_crypto,
        "volatility":       True,
        "news_filter":      True,
        "risk_filter":      True,
        # Structure
        "ema200":           True,
        "bos":              is_metal,
        "fvg":              is_metal,
        "tick_jump":        is_crypto or is_metal,
        "liq_vacuum":       is_metal,
        "vwap":             is_crypto or is_metal or is_index,
        # Technical
        "macd":             is_fx or is_index,
        "bollinger":        False,
        "adx":              is_metal or is_fx,
        "volume":           is_crypto or is_index,
        "swing":            is_fx_maj,
        # Trend
        "ema_crossover":    is_fx_maj or is_index,
        "alma_filter":      is_metal,
        "trendilo":         is_metal or is_fx,
        # Momentum
        "rsi_feature":      True,
        "fast_fingers":     is_crypto or is_metal or is_index,
        "choppiness_index": True,
        # Macro
        "dxy_filter":       is_metal or is_fx_maj,
        "sentiment_filter": is_crypto,
        "correlation_guard": True,
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
                stored = json.loads(raw)
                if len(stored) < len(_ALL_TOOLS):
                    # Stored data is stale (missing new tools) — migrate to full defaults
                    tools = _default_tools(ac)
                    await repo.set_many({f"ASSET_TOOLS_{symbol}": json.dumps(tools)})
                else:
                    tools = stored
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


def _require_operator_auth(authorization: str) -> None:
    """Require bearer auth for asset tool writes when AUTH_TOKEN is configured."""
    expected = os.environ.get("AUTH_TOKEN", "")
    if not expected:
        return
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or provided.strip() != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/{symbol}/tools/reset")
async def reset_asset_tools(
    symbol: str,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Reset a symbol's tool preset to class defaults."""
    _require_operator_auth(authorization)
    symbol = symbol.upper()
    if symbol not in ASSETS:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not in asset library")
    ac = ASSETS[symbol]
    tools = _default_tools(ac)
    repo = SettingsRepository(session)
    await repo.set_many({f"ASSET_TOOLS_{symbol}": json.dumps(tools)})
    session.add(OperatorAuditLog(
        operator="webui",
        action="asset_tools_reset",
        target=symbol,
        old_value=None,
        new_value=json.dumps(tools, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    ))
    await session.commit()
    return {"status": "ok", "symbol": symbol, "tools": tools}


@router.put("/{symbol}/tools")
async def update_asset_tools(
    symbol: str,
    body: ToolsUpdate,
    request: Request,
    authorization: str = Header(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Save the filter/tool preset for a symbol."""
    _require_operator_auth(authorization)
    symbol = symbol.upper()
    if symbol not in ASSETS:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not in asset library")
    repo = SettingsRepository(session)
    old_value = await repo.get(f"ASSET_TOOLS_{symbol}", "")
    # Only persist recognised tool keys
    clean = {k: bool(v) for k, v in body.tools.items() if k in _ALL_TOOLS}
    await repo.set_many({f"ASSET_TOOLS_{symbol}": json.dumps(clean)})
    session.add(OperatorAuditLog(
        operator="webui",
        action="asset_tools_update",
        target=symbol,
        old_value=old_value or None,
        new_value=json.dumps(clean, sort_keys=True),
        source_ip=request.client.host if request and request.client else "unknown",
    ))
    await session.commit()
    return {"status": "ok", "symbol": symbol, "tools": clean}
