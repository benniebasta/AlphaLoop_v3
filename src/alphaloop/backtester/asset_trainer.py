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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.backtester.engine import BacktestEngine
from alphaloop.backtester.params import BacktestParams
from alphaloop.backtester.runner import make_signal_fn, _fetch_data, _log
from alphaloop.core.types import StrategyStatus
from alphaloop.db.repositories.backtest_repo import BacktestRepository

logger = logging.getLogger(__name__)

STRATEGY_VERSIONS_DIR = Path("strategy_versions")


def _compute_fingerprint(params, tools, validation, ai_models) -> str:
    """Deterministic SHA256 hash of strategy config for change detection."""
    canonical = json.dumps(
        {"params": params, "tools": tools, "validation": validation, "ai_models": ai_models},
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
) -> dict[str, Any]:
    """
    Create a strategy version JSON file in strategy_versions/.

    Returns the version dict including the file path.
    """
    version = _next_version(symbol)
    STRATEGY_VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Build tool toggles
    all_tools = [
        "session_filter", "news_filter", "volatility_filter",
        "dxy_filter", "sentiment_filter", "risk_filter",
        "bos_guard", "fvg_guard", "vwap_guard", "correlation_guard",
        "macd_filter", "bollinger_filter", "adx_filter",
        "volume_filter", "swing_structure",
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
        "claude_enabled": True,
    }

    version_data = {
        "symbol": symbol,
        "version": version,
        "name": name or f"{symbol}_v{version}",
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
        },
        "summary": {
            "total_trades": metrics.get("total_trades", 0),
            "win_rate": round(metrics.get("win_rate", 0), 3),
            "sharpe": round(metrics.get("sharpe", 0) or 0, 3),
            "max_dd_pct": round(metrics.get("max_drawdown_pct", 0) or 0, 1),
            "total_pnl": round(metrics.get("total_pnl", 0) or 0, 2),
        },
        "status": status,
        "tools": tool_config,
        "validation": validation_config,
        "ai_models": ai_models or {
            "signal": "gemini-2.5-flash",
            "validator": "claude-sonnet-4-6",
            "research": "claude-sonnet-4-6",
            "autolearn": "gemini-2.5-flash",
            "fallback": "gpt-4o-mini",
        },
        "signal_mode": "algo_plus_ai",
        "fingerprint": "",  # computed below after ai_models resolved
    }

    # Compute fingerprint from resolved data
    version_data["fingerprint"] = _compute_fingerprint(
        version_data["params"], version_data["tools"],
        version_data["validation"], version_data["ai_models"],
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

    # Build base params from card
    base_params = BacktestParams(
        ema_fast=base_params_dict.get("ema_fast", 21),
        ema_slow=base_params_dict.get("ema_slow", 55),
        sl_atr_mult=base_params_dict.get("sl_atr_mult", 2.0),
        tp1_rr=base_params_dict.get("tp1_rr", 2.0),
        tp2_rr=base_params_dict.get("tp2_rr", 4.0),
        rsi_ob=base_params_dict.get("rsi_ob", 70.0),
        rsi_os=base_params_dict.get("rsi_os", 30.0),
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

    engine = BacktestEngine(session_factory=session_factory)
    best_params = base_params
    best_sharpe = -999.0
    best_result = None

    train_data, val_data = split_data(opens, highs, lows, closes, timestamps)

    # Baseline
    _log_fn("Running baseline...")
    sig_fn = make_signal_fn(base_params, filters)
    result = await engine.run(
        symbol=symbol,
        opens=opens, highs=highs, lows=lows, closes=closes,
        timestamps=timestamps, balance=balance,
        risk_pct=base_params.risk_pct, filters=filters,
        signal_fn=sig_fn,
        stop_check=stop_check,
    )
    best_sharpe = result.sharpe or -999.0
    best_result = result
    _log_fn(f"Baseline: {len(result.closed_trades)} trades, Sharpe={best_sharpe:.3f}")

    # Optimize
    no_improve = 0
    for gen in range(2, max_generations + 1):
        if stop_check and stop_check():
            break

        _log_fn(f"Generation {gen}/{max_generations}...")

        def run_on_train(params: BacktestParams) -> float:
            sig_fn = make_signal_fn(params, filters)
            try:
                r = asyncio.run(engine.run(
                    symbol=symbol,
                    opens=train_data["opens"], highs=train_data["highs"],
                    lows=train_data["lows"], closes=train_data["closes"],
                    timestamps=train_data["timestamps"], balance=balance,
                    risk_pct=params.risk_pct, filters=filters, signal_fn=sig_fn,
                    stop_check=stop_check,
                ))
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
        sig_fn_val = make_signal_fn(opt_params, filters)
        val_result = await engine.run(
            symbol=symbol,
            opens=val_data["opens"], highs=val_data["highs"],
            lows=val_data["lows"], closes=val_data["closes"],
            timestamps=val_data["timestamps"], balance=balance,
            risk_pct=opt_params.risk_pct, filters=filters, signal_fn=sig_fn_val,
        )
        gap = train_sharpe - (val_result.sharpe or -999.0)

        if gap > OVERFIT_GAP_THRESHOLD:
            _log_fn(f"  Overfit detected (gap={gap:.3f})")
            no_improve += 1
            continue

        # Full data confirmation
        sig_fn_full = make_signal_fn(opt_params, filters)
        full_result = await engine.run(
            symbol=symbol,
            opens=opens, highs=highs, lows=lows, closes=closes,
            timestamps=timestamps, balance=balance,
            risk_pct=opt_params.risk_pct, filters=filters, signal_fn=sig_fn_full,
            stop_check=stop_check,
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
        "total_trades": len(best_result.closed_trades),
        "win_rate": best_result.win_rate or 0,
        "sharpe": best_result.sharpe or 0,
        "max_drawdown_pct": best_result.max_drawdown_pct or 0,
        "total_pnl": best_result.total_pnl or 0,
    }

    version_data = create_strategy_version(
        symbol=symbol,
        params=best_params,
        metrics=metrics,
        tools=filters,
        status="candidate",
        source="asset_trainer",
        seed_hash=card_dict.get("seed_hash"),
    )

    _log_fn(f"Created version: {symbol} v{version_data['_version']}")

    return {
        "success": True,
        "version_data": version_data,
        "best_params": best_params.model_dump(),
        "best_sharpe": best_sharpe,
        "metrics": metrics,
    }
