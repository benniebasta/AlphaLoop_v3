"""
backtester/asset_trainer.py — Strategy training orchestrator.

Bridges the full lifecycle:
  SeedLab card -> Optuna optimization -> Strategy version file -> DB registration

This is the missing link between strategy discovery (SeedLab) and deployment
(DeploymentPipeline). It takes a strategy card's filter config and parameters,
runs Optuna optimization on them, and produces a versioned strategy JSON file
in strategy_versions/.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.backtester.params import BacktestParams
from alphaloop.backtester.runner import _run_vbt, _fetch_data, _log
from alphaloop.config.assets import get_asset_config
from alphaloop.core.constants import STRATEGY_VERSIONS_DIR
from alphaloop.core.types import StrategyStatus
from alphaloop.db.repositories.backtest_repo import BacktestRepository

logger = logging.getLogger(__name__)

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


def _generate_card_name(symbol: str, signal_mode: str) -> str:
    """Generate a friendly card name when the user leaves it blank."""
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    suffix = "ai" if signal_mode == "ai_signal" else signal_mode
    return f"{adj}-{noun}-{symbol}_{suffix}"


def _default_ai_signal_prompts(symbol: str) -> dict[str, str]:
    """Create starter prompt instructions for a new AI_SIGNAL card."""
    asset = get_asset_config(symbol)
    signal_instruction = (
        f"You are the dedicated AI signal engine for {asset.display_name} ({asset.symbol}). "
        "Generate one high-quality trade idea only when the setup is clean and the edge is clear. "
        "Use market structure, higher-timeframe bias, session context, news risk, and DXY/sentiment only as supporting evidence. "
        "Return strict JSON and nothing else. If no valid setup exists, return a neutral HOLD outcome."
    )
    validator_instruction = (
        f"You are the AI validator for {asset.display_name} ({asset.symbol}). "
        "Be conservative and capital-preserving. Reject low-quality, news-exposed, overextended, or poorly structured setups. "
        "Only approve signals with clear edge, valid risk:reward, and coherent stop-loss placement. "
        "Return strict JSON and nothing else."
    )
    return {
        "signal_instruction": signal_instruction,
        "validator_instruction": validator_instruction,
    }


def _compute_fingerprint(params, tools, validation, ai_models, signal_instruction="", validator_instruction="") -> str:
    """Deterministic SHA256 hash of strategy config for change detection."""
    canonical = json.dumps(
        {
            "params": params,
            "tools": tools,
            "validation": validation,
            "ai_models": ai_models,
            "signal_instruction": signal_instruction,
            "validator_instruction": validator_instruction,
        },
        sort_keys=True, default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _next_version(symbol: str) -> int:
    """Determine the next version number for a symbol by scanning existing files."""
    STRATEGY_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    versions = []
    for f in STRATEGY_VERSIONS_DIR.glob(f"{symbol}_v*.json"):
        try:
            v = int(f.stem.split("_v")[-1])
            versions.append(v)
        except (ValueError, IndexError):
            continue
    return max(versions, default=0) + 1


def create_strategy_version(
    symbol: str,
    params: BacktestParams,
    metrics: dict[str, Any],
    tools: list[str],
    status: str = "candidate",
    source: str = "backtest",
    ai_models: dict[str, str] | None = None,
    seed_hash: str | None = None,
    name: str = "",
    timeframe: str = "1h",
    days: int = 365,
    initial_capital: float = 10000.0,
    signal_mode: str = "algo_ai",
    signal_instruction: str = "",
    validator_instruction: str = "",
) -> dict[str, Any]:
    """
    Create a strategy version JSON file in strategy_versions/.

    Returns the version dict including the file path.
    """
    version = _next_version(symbol)
    STRATEGY_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    normalized_mode = signal_mode.strip().lower()
    if normalized_mode == "ai_signal":
        defaults = _default_ai_signal_prompts(symbol)
        signal_instruction = signal_instruction or defaults["signal_instruction"]
        validator_instruction = validator_instruction or defaults["validator_instruction"]

    if not name:
        name = _generate_card_name(symbol, normalized_mode) if normalized_mode == "ai_signal" else f"{symbol}_v{version}"

    # Build tool toggles
    all_tools = [
        "session_filter", "news_filter", "volatility_filter",
        "dxy_filter", "sentiment_filter", "risk_filter",
        "ema200_filter", "macd_filter", "bollinger_filter", "adx_filter",
        "volume_filter", "swing_structure", "tick_jump_guard", "liq_vacuum_guard",
        "bos_guard", "fvg_guard", "vwap_guard", "correlation_guard",
        "ema_crossover", "rsi_feature", "trendilo", "fast_fingers",
        "choppiness_index", "alma_filter",
    ]
    tool_config = {t: (t in tools) for t in all_tools}

    # Build validation overrides from params
    validation_config = {
        "min_confidence": 0.6,
        "min_rr": params.tp1_rr * 0.5 if params.tp1_rr > 0 else 0.9,
        "check_rsi": True,
        "rsi_ob": params.rsi_ob,
        "rsi_os": params.rsi_os,
        "check_ema200_trend": "ema200_filter" in tools,
        "check_bos": "bos_guard" in tools,
        "check_fvg": "fvg_guard" in tools,
        "check_tick_jump": True,
        "check_liq_vacuum": True,
        "check_regime": True,
        "validation_level": "strict",
    }

    version_data = {
        "symbol": symbol,
        "version": version,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "seed_hash": seed_hash,
        "params": {
            "ema_fast": params.ema_fast,
            "ema_slow": params.ema_slow,
            "sl_atr_mult": round(params.sl_atr_mult, 3),
            "tp1_rr": round(params.tp1_rr, 3),
            "tp2_rr": round(params.tp2_rr, 3),
            "tp1_close_pct": 0.6,
            "rsi_period": params.rsi_period,
            "rsi_ob": params.rsi_ob,
            "rsi_os": params.rsi_os,
            "risk_pct": params.risk_pct,
            "macd_fast": params.macd_fast,
            "macd_slow": params.macd_slow,
            "macd_signal": params.macd_signal,
            "bb_period": params.bb_period,
            "bb_std_dev": round(params.bb_std_dev, 3),
            "adx_period": params.adx_period,
            "adx_min_threshold": round(params.adx_min_threshold, 1),
            "volume_ma_period": params.volume_ma_period,
            "signal_rules": list(params.signal_rules or [{"source": "ema_crossover"}]),
            "signal_logic": params.signal_logic or "AND",
        },
        "summary": {
            "total_trades": metrics.get("total_trades", 0),
            "win_rate": round(metrics.get("win_rate", 0), 3),
            "sharpe": round(metrics.get("sharpe", 0) or 0, 3),
            "max_dd_pct": round(metrics.get("max_drawdown_pct", 0) or 0, 1),
            "total_pnl": round(metrics.get("total_pnl", 0) or 0, 2),
            "timeframe": timeframe,
            "days": days,
            "initial_capital": initial_capital,
        },
        "status": status,
        "tools": tool_config,
        "validation": validation_config,
        "ai_models": ai_models or {
            "signal":        "gemini-2.5-flash-lite",   # ai_signal generation (cheap + fast)
            "validator":     "claude-haiku-4-5-20251001", # gate: structured approve/reject
            "research":      "gemini-2.5-pro",           # deep degradation analysis
            "param_suggest": "deepseek-reasoner",        # parameter change reasoning
            "regime":        "gemini-2.5-flash-lite",    # hourly regime classification
            "fallback":      "grok-3-mini",              # provider-down fallback
        },
        "signal_mode": normalized_mode,
        "signal_instruction": signal_instruction,
        "validator_instruction": validator_instruction,
        "scoring_weights": {},
        "confidence_thresholds": {},
        "fingerprint": "",  # computed below after ai_models resolved
    }

    # Compute fingerprint from resolved data
    version_data["fingerprint"] = _compute_fingerprint(
        version_data["params"], version_data["tools"],
        version_data["validation"], version_data["ai_models"],
        version_data["signal_instruction"], version_data["validator_instruction"],
    )

    # Write atomically
    path = STRATEGY_VERSIONS_DIR / f"{symbol}_v{version}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(version_data, indent=2))
    tmp.replace(path)

    logger.info(
        "Created strategy version: %s v%d (status=%s, sharpe=%.3f, WR=%.1f%%)",
        symbol, version, status,
        version_data["summary"]["sharpe"],
        version_data["summary"]["win_rate"] * 100,
    )

    version_data["_path"] = str(path)
    version_data["_version"] = version
    return version_data


async def train_from_card(
    card_dict: dict[str, Any],
    symbol: str,
    days: int = 365,
    balance: float = 10_000.0,
    max_generations: int = 5,
    session_factory: async_sessionmaker | None = None,
    timeframe: str = "1h",
    signal_mode: str = "algo_ai",
    stop_check: Any = None,
    log_fn: Any = None,
) -> dict[str, Any]:
    """
    Train (optimize) a strategy from a SeedLab card.

    Takes a card's filters and params, runs Optuna optimization,
    and creates a strategy version JSON.

    Args:
        card_dict: Strategy card dict with 'filters', 'params', 'name'.
        symbol: Trading symbol.
        days: Historical data days.
        balance: Starting balance.
        max_generations: Optuna optimization generations.
        session_factory: DB session factory (optional).
        timeframe: Data timeframe.
        stop_check: Callable returning True to abort.
        log_fn: Logging callback (msg) -> None.

    Returns:
        dict with 'version_data', 'best_params', 'best_sharpe', 'success'.
    """
    import asyncio
    from alphaloop.backtester.optimizer import (
        optimize, split_data, MIN_SHARPE_IMPROVEMENT, OVERFIT_GAP_THRESHOLD,
    )

    _log_fn = log_fn or (lambda msg: logger.info(msg))

    filters = card_dict.get("filters", [])
    base_params_dict = card_dict.get("params", {})

    # Build base params from card (full extraction including per-source params)
    base_params = BacktestParams(
        ema_fast=base_params_dict.get("ema_fast", 21),
        ema_slow=base_params_dict.get("ema_slow", 55),
        sl_atr_mult=base_params_dict.get("sl_atr_mult", 2.0),
        tp1_rr=base_params_dict.get("tp1_rr", 2.0),
        tp2_rr=base_params_dict.get("tp2_rr", 4.0),
        rsi_period=base_params_dict.get("rsi_period", 14),
        rsi_ob=base_params_dict.get("rsi_ob", 70.0),
        rsi_os=base_params_dict.get("rsi_os", 30.0),
        macd_fast=base_params_dict.get("macd_fast", 12),
        macd_slow=base_params_dict.get("macd_slow", 26),
        macd_signal=base_params_dict.get("macd_signal", 9),
        bb_period=base_params_dict.get("bb_period", 20),
        bb_std_dev=base_params_dict.get("bb_std_dev", 2.0),
        adx_period=base_params_dict.get("adx_period", 14),
        adx_min_threshold=base_params_dict.get("adx_min_threshold", 20.0),
        volume_ma_period=base_params_dict.get("volume_ma_period", 20),
        risk_pct=base_params_dict.get("risk_pct", 0.01),
        signal_rules=base_params_dict.get("signal_rules", [{"source": "ema_crossover"}]),
        signal_logic=base_params_dict.get("signal_logic", "AND"),
    )

    run_id = f"train_{symbol}_{int(time.time())}"
    _log_fn(f"Training from card: {card_dict.get('name', 'unknown')}")
    _log_fn(f"Filters: {', '.join(filters) if filters else 'none'}")

    # Fetch data
    _log_fn(f"Fetching {days}d of {symbol} data ({timeframe})...")
    try:
        opens, highs, lows, closes, timestamps = await _fetch_data(
            symbol, days, run_id, timeframe
        )
    except Exception as e:
        return {"success": False, "error": f"Data fetch failed: {e}"}

    _log_fn(f"Loaded {len(closes)} bars")

    best_params = base_params
    best_sharpe = -999.0
    best_result = None

    train_data, val_data = split_data(opens, highs, lows, closes, timestamps)

    # Baseline
    _log_fn("Running baseline...")
    result = await asyncio.to_thread(
        _run_vbt,
        symbol, opens, highs, lows, closes, timestamps, balance, base_params,
    )
    best_sharpe = result.sharpe or -999.0
    best_result = result
    _log_fn(f"Baseline: {result.trade_count} trades, Sharpe={best_sharpe:.3f}")

    # Optimize
    no_improve = 0
    for gen in range(2, max_generations + 1):
        if stop_check and stop_check():
            break

        _log_fn(f"Generation {gen}/{max_generations}...")

        def run_on_train(params: BacktestParams) -> float:
            try:
                r = _run_vbt(
                    symbol,
                    train_data["opens"], train_data["highs"],
                    train_data["lows"], train_data["closes"],
                    train_data["timestamps"], balance, params,
                )
                return r.sharpe or -999.0
            except Exception:
                return -999.0

        opt_params, train_sharpe, was_stopped = await asyncio.to_thread(
            optimize, best_params, run_on_train, 30, stop_check,
            lambda msg: _log_fn(f"  {msg}"),
        )

        if was_stopped:
            break

        if opt_params is None or train_sharpe <= best_sharpe + MIN_SHARPE_IMPROVEMENT:
            no_improve += 1
            if no_improve >= 2:
                _log_fn("Early stop — no improvement")
                break
            continue

        # Validate
        val_result = await asyncio.to_thread(
            _run_vbt,
            symbol,
            val_data["opens"], val_data["highs"],
            val_data["lows"], val_data["closes"],
            val_data["timestamps"], balance, opt_params,
        )
        gap = train_sharpe - (val_result.sharpe or -999.0)

        if gap > OVERFIT_GAP_THRESHOLD:
            _log_fn(f"  Overfit detected (gap={gap:.3f})")
            no_improve += 1
            continue

        # Full data confirmation
        full_result = await asyncio.to_thread(
            _run_vbt,
            symbol, opens, highs, lows, closes, timestamps, balance, opt_params,
        )
        full_sharpe = full_result.sharpe or -999.0

        if full_sharpe > best_sharpe + MIN_SHARPE_IMPROVEMENT:
            best_sharpe = full_sharpe
            best_params = opt_params
            best_result = full_result
            no_improve = 0
            _log_fn(f"  Accepted: Sharpe={full_sharpe:.3f}")
        else:
            no_improve += 1

    # Create strategy version file
    if best_result is None:
        return {"success": False, "error": "No valid backtest result"}

    metrics = {
        "total_trades": best_result.trade_count,
        "win_rate": best_result.win_rate or 0,
        "sharpe": best_result.sharpe or 0,
        "max_drawdown_pct": best_result.max_drawdown_pct or 0,
        "total_pnl": best_result.total_pnl or 0,
    }

    # Walk-forward promotion gate (S-01) ─────────────────────────────────────
    # Build a DataFrame for the walk-forward engine from raw arrays.
    wf_passed = False
    wf_result = None
    wf_reason = "walk-forward not run"
    try:
        import pandas as pd
        from alphaloop.backtester.walk_forward import run_walk_forward
        from alphaloop.config.assets import get_asset_config as _get_ac

        _ac = _get_ac(symbol)
        ts_index = pd.to_datetime(timestamps) if timestamps else None
        ohlcv_df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes},
            index=ts_index,
        )

        _log_fn("Running walk-forward gate (70% IS / 30% OOS)...")
        wf_result = await asyncio.to_thread(
            run_walk_forward,
            ohlcv_df, best_params, _ac,
            symbol=symbol,
            log_fn=_log_fn,
        )
        wf_passed = wf_result.passes_gate
        wf_reason = wf_result.gate_reason
        _log_fn(
            f"Walk-forward gate: {'PASSED' if wf_passed else 'FAILED'} — {wf_reason}"
        )
    except Exception as _wf_exc:
        _log_fn(f"Walk-forward gate skipped (error): {_wf_exc}")
        logger.warning("[asset_trainer] Walk-forward gate error: %s", _wf_exc)
        # Fail-open: let candidate through if walk-forward itself errors
        wf_passed = True
        wf_reason = f"gate skipped (error): {_wf_exc}"

    # Gate status: passed → "candidate", failed → "wf_rejected"
    version_status = "candidate" if wf_passed else "wf_rejected"
    if not wf_passed:
        _log_fn(
            f"Strategy version will be written with status='wf_rejected' — "
            "review walk-forward results before deploying."
        )

    version_data = create_strategy_version(
        symbol=symbol,
        params=best_params,
        metrics=metrics,
        tools=filters,
        status=version_status,
        source="asset_trainer",
        seed_hash=card_dict.get("seed_hash"),
        signal_mode=signal_mode,
    )

    _log_fn(f"Created version: {symbol} v{version_data['_version']}")

    return {
        "success": True,
        "version_data": version_data,
        "best_params": best_params.model_dump(),
        "best_sharpe": best_sharpe,
        "metrics": metrics,
        "walk_forward": wf_result.summary() if wf_result else {"passed": wf_passed, "reason": wf_reason},
        "walk_forward_passed": wf_passed,
    }
