"""
GET/PUT/DELETE/POST /api/asset-params — Per-asset, per-timeframe parameter management.

Provides a UI-editable layer on top of the baked-in AssetConfig.default_params_by_timeframe.
User edits are persisted in the settings DB and merged at runtime via resolve_construction_params().

DB key pattern:
  ASSET_TF_PARAMS_{SYMBOL}_{TF}  → JSON blob of param overrides for that symbol+TF
  ASSET_CUSTOM_{SYMBOL}           → JSON blob of full custom AssetConfig-like dict
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.config.assets import ASSETS, AssetConfig
from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/asset-params", tags=["asset_params"])

_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]

_CONSTRUCTION_KEYS = [
    "sl_min_points", "sl_max_points", "sl_atr_mult", "sl_buffer_atr",
    "tp1_rr", "tp2_rr", "entry_zone_atr_mult",
]


def _db_key_tf(symbol: str, tf: str) -> str:
    return f"ASSET_TF_PARAMS_{symbol.upper()}_{tf}"


def _db_key_custom(symbol: str) -> str:
    return f"ASSET_CUSTOM_{symbol.upper()}"


def _merge_tf_params(asset_cfg: AssetConfig, db_overrides: dict, tf: str) -> dict:
    """Merge asset TF default with DB overrides for a single timeframe."""
    base: dict[str, Any] = {}
    # Start from asset-level baseline (construction params)
    for k in _CONSTRUCTION_KEYS:
        v = getattr(asset_cfg, k, None)
        if v is not None:
            base[k] = v
    # tp1_rr not always on AssetConfig directly
    base.setdefault("tp1_rr", getattr(asset_cfg, "tp1_rr", 1.5))
    base.setdefault("sl_atr_mult", getattr(asset_cfg, "sl_atr_mult", 1.5))
    base.setdefault("entry_zone_atr_mult", getattr(asset_cfg, "entry_zone_atr_mult", 0.25))
    base.setdefault("sl_buffer_atr", 0.15)
    # Apply baked-in TF defaults
    tf_defaults = (getattr(asset_cfg, "default_params_by_timeframe", None) or {}).get(tf, {})
    for k, v in tf_defaults.items():
        if k in _CONSTRUCTION_KEYS or k == "tools_config":
            base[k] = v
    # Apply DB overrides (user edits)
    is_overridden = tf in db_overrides
    if is_overridden:
        for k, v in db_overrides[tf].items():
            base[k] = v
    base["is_overridden"] = is_overridden
    return base


async def _load_db_overrides(symbol: str, repo: SettingsRepository) -> dict:
    """Load all DB TF overrides for a symbol. Returns {tf: {...params}}."""
    result: dict[str, dict] = {}
    for tf in _TIMEFRAMES:
        raw = await repo.get(_db_key_tf(symbol, tf), "")
        if raw:
            try:
                result[tf] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return result


def _asset_to_response(
    symbol: str,
    asset_cfg: AssetConfig,
    db_overrides: dict,
    is_custom: bool = False,
) -> dict:
    return {
        "symbol": symbol,
        "display_name": asset_cfg.display_name,
        "asset_class": asset_cfg.asset_class,
        "pip_size": asset_cfg.pip_size,
        "sl_atr_mult": asset_cfg.sl_atr_mult,
        "tp1_rr": getattr(asset_cfg, "tp1_rr", 1.5),
        "is_custom": is_custom,
        "timeframes": {
            tf: _merge_tf_params(asset_cfg, db_overrides, tf)
            for tf in _TIMEFRAMES
        },
    }


@router.get("")
async def list_asset_params(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all assets with their resolved per-TF params (baked-in + DB overrides)."""
    repo = SettingsRepository(session)
    assets_list = []

    # Built-in assets
    for symbol, asset_cfg in ASSETS.items():
        db_overrides = await _load_db_overrides(symbol, repo)
        assets_list.append(_asset_to_response(symbol, asset_cfg, db_overrides, is_custom=False))

    # Custom assets stored in DB
    all_settings = await repo.get_all()
    for key, value in all_settings.items():
        if not key.startswith("ASSET_CUSTOM_"):
            continue
        symbol = key[len("ASSET_CUSTOM_"):]
        if symbol in ASSETS:
            continue  # skip if somehow overlaps with built-in
        try:
            cfg_data = json.loads(value)
        except json.JSONDecodeError:
            continue
        # Build a minimal AssetConfig-like object from the stored dict
        try:
            custom_cfg = AssetConfig(**{
                k: v for k, v in cfg_data.items()
                if k in AssetConfig.model_fields
            })
        except Exception:
            continue
        db_overrides = await _load_db_overrides(symbol, repo)
        assets_list.append(_asset_to_response(symbol, custom_cfg, db_overrides, is_custom=True))

    return {"assets": assets_list}


class TFParamUpdate(BaseModel):
    params: dict


@router.put("/{symbol}/{timeframe}")
async def update_tf_params(
    symbol: str,
    timeframe: str,
    body: TFParamUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Save TF param overrides for a symbol to the settings DB."""
    symbol = symbol.upper()
    timeframe = timeframe.upper()
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(422, f"Unknown timeframe '{timeframe}'. Must be one of: {_TIMEFRAMES}")

    # Validate only known construction/tools keys
    allowed = set(_CONSTRUCTION_KEYS) | {"tools_config"}
    unknown = set(body.params.keys()) - allowed
    if unknown:
        raise HTTPException(422, f"Unknown param keys: {sorted(unknown)}")

    repo = SettingsRepository(session)
    await repo.set(_db_key_tf(symbol, timeframe), json.dumps(body.params))
    return {"ok": True, "symbol": symbol, "timeframe": timeframe, "params": body.params}


@router.delete("/{symbol}/{timeframe}")
async def reset_tf_params(
    symbol: str,
    timeframe: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove DB override for a symbol+TF, restoring baked-in defaults."""
    symbol = symbol.upper()
    timeframe = timeframe.upper()
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(422, f"Unknown timeframe '{timeframe}'")

    repo = SettingsRepository(session)
    # Set to empty string = no override
    await repo.set(_db_key_tf(symbol, timeframe), "")
    return {"ok": True, "symbol": symbol, "timeframe": timeframe, "reset": True}


class NewAssetRequest(BaseModel):
    symbol: str
    display_name: str
    asset_class: str
    pip_size: float
    sl_atr_mult: float = 1.5
    tp1_rr: float = 1.5
    tp2_rr: float = 2.5
    sl_min_points: float = 100.0
    sl_max_points: float = 1000.0
    max_spread_points: float = 50.0
    mt5_symbol: str = ""


@router.post("")
async def add_custom_asset(
    body: NewAssetRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Add a new custom asset (stored in DB, not in code)."""
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(422, "symbol is required")
    if symbol in ASSETS:
        raise HTTPException(409, f"Symbol '{symbol}' already exists as a built-in asset")

    cfg_data = {
        "symbol": symbol,
        "display_name": body.display_name or symbol,
        "asset_class": body.asset_class or "unknown",
        "mt5_symbol": body.mt5_symbol or symbol,
        "pip_size": body.pip_size,
        "sl_atr_mult": body.sl_atr_mult,
        "tp1_rr": body.tp1_rr,
        "tp2_rr": body.tp2_rr,
        "sl_min_points": body.sl_min_points,
        "sl_max_points": body.sl_max_points,
        "max_spread_points": body.max_spread_points,
    }
    repo = SettingsRepository(session)
    await repo.set(_db_key_custom(symbol), json.dumps(cfg_data))
    return {"ok": True, "symbol": symbol, "created": True}


@router.delete("/{symbol}")
async def delete_custom_asset(
    symbol: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a custom asset. Built-in assets cannot be deleted via API."""
    symbol = symbol.upper()
    if symbol in ASSETS:
        raise HTTPException(403, f"'{symbol}' is a built-in asset and cannot be deleted via API")
    repo = SettingsRepository(session)
    await repo.set(_db_key_custom(symbol), "")
    # Also clear any TF overrides
    for tf in _TIMEFRAMES:
        await repo.set(_db_key_tf(symbol, tf), "")
    return {"ok": True, "symbol": symbol, "deleted": True}
