"""
Persistence layer for stateful risk guards.

Serializes/deserializes guard state to/from the app_settings table
so that rolling windows, counters, and pause timers survive restarts.
"""

import json
import logging
from collections import deque
from datetime import datetime, timezone

from alphaloop.risk.guards import (
    ConfidenceVarianceFilter,
    DrawdownPauseGuard,
    EquityCurveScaler,
    SignalHashFilter,
    SpreadRegimeFilter,
)

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "risk_guard_state"


def serialize_guards(
    hash_filter: SignalHashFilter | None = None,
    conf_variance: ConfidenceVarianceFilter | None = None,
    spread_regime: SpreadRegimeFilter | None = None,
    equity_scaler: EquityCurveScaler | None = None,
    dd_pause: DrawdownPauseGuard | None = None,
) -> str:
    """Serialize guard states to JSON string for DB storage."""
    state: dict = {}

    if hash_filter:
        state["hash_filter"] = {
            "hashes": list(hash_filter._hashes),
            "window": hash_filter.window,
        }

    if conf_variance:
        state["conf_variance"] = {
            "confs": list(conf_variance._confs),
            "window": conf_variance.window,
            "max_stdev": conf_variance.max_stdev,
        }

    if spread_regime:
        state["spread_regime"] = {
            "spreads": list(spread_regime._spreads),
            "threshold": spread_regime.threshold,
        }

    if equity_scaler:
        state["equity_scaler"] = {
            "pnl": list(equity_scaler._pnl),
            "window": equity_scaler.window,
        }

    if dd_pause:
        # Serialize per-symbol recent trades
        recent_data = {}
        for sym, dq in dd_pause._recent.items():
            recent_data[sym] = [list(pair) for pair in dq]
        # Serialize per-symbol pause expiry + global pause
        paused_data = {}
        for sym, dt in dd_pause._paused_until.items():
            paused_data[sym] = dt.isoformat()
        state["dd_pause"] = {
            "recent": recent_data,
            "default_pause_minutes": dd_pause._default_pause_minutes,
            "paused_until": paused_data,
            "global_pause_until": dd_pause._global_pause_until.isoformat()
            if dd_pause._global_pause_until
            else None,
        }

    state["saved_at"] = datetime.now(timezone.utc).isoformat()
    return json.dumps(state)


def restore_guards(
    json_str: str,
    hash_filter: SignalHashFilter | None = None,
    conf_variance: ConfidenceVarianceFilter | None = None,
    spread_regime: SpreadRegimeFilter | None = None,
    equity_scaler: EquityCurveScaler | None = None,
    dd_pause: DrawdownPauseGuard | None = None,
) -> None:
    """Restore guard states from JSON string."""
    try:
        state = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[guard-persistence] Invalid state JSON — starting fresh")
        return

    if hash_filter and "hash_filter" in state:
        data = state["hash_filter"]
        hash_filter._hashes = deque(data.get("hashes", []), maxlen=hash_filter.window)

    if conf_variance and "conf_variance" in state:
        data = state["conf_variance"]
        conf_variance._confs = deque(
            data.get("confs", []), maxlen=conf_variance.window
        )

    if spread_regime and "spread_regime" in state:
        data = state["spread_regime"]
        spread_regime._spreads = deque(
            data.get("spreads", []), maxlen=spread_regime._spreads.maxlen
        )

    if equity_scaler and "equity_scaler" in state:
        data = state["equity_scaler"]
        equity_scaler._pnl = deque(
            data.get("pnl", []), maxlen=equity_scaler.window
        )

    if dd_pause and "dd_pause" in state:
        data = state["dd_pause"]
        # Restore per-symbol recent trades
        for sym, pairs in data.get("recent", {}).items():
            dq = deque(maxlen=5)
            for pair in pairs:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    dq.append(tuple(pair))
            dd_pause._recent[sym] = dq
        # Restore per-symbol pause expiry
        for sym, dt_str in data.get("paused_until", {}).items():
            try:
                dd_pause._paused_until[sym] = datetime.fromisoformat(dt_str)
            except (ValueError, TypeError):
                pass
        # Restore global pause
        global_pause = data.get("global_pause_until")
        if global_pause:
            try:
                dd_pause._global_pause_until = datetime.fromisoformat(global_pause)
            except (ValueError, TypeError):
                dd_pause._global_pause_until = None

    saved_at = state.get("saved_at", "unknown")
    logger.info("[guard-persistence] Restored guard state from %s", saved_at)


async def save_guard_state(
    settings_service,
    hash_filter: SignalHashFilter | None = None,
    conf_variance: ConfidenceVarianceFilter | None = None,
    spread_regime: SpreadRegimeFilter | None = None,
    equity_scaler: EquityCurveScaler | None = None,
    dd_pause: DrawdownPauseGuard | None = None,
) -> None:
    """Save guard state to DB via settings service."""
    try:
        data = serialize_guards(
            hash_filter, conf_variance, spread_regime, equity_scaler, dd_pause
        )
        await settings_service.set(_SETTINGS_KEY, data)
    except Exception as e:
        logger.error("[guard-persistence] Save failed: %s", e)


async def load_guard_state(
    settings_service,
    hash_filter: SignalHashFilter | None = None,
    conf_variance: ConfidenceVarianceFilter | None = None,
    spread_regime: SpreadRegimeFilter | None = None,
    equity_scaler: EquityCurveScaler | None = None,
    dd_pause: DrawdownPauseGuard | None = None,
) -> None:
    """Load guard state from DB via settings service."""
    try:
        data = await settings_service.get(_SETTINGS_KEY)
        if data:
            restore_guards(
                data, hash_filter, conf_variance, spread_regime,
                equity_scaler, dd_pause,
            )
    except Exception as e:
        logger.warning("[guard-persistence] Load failed (starting fresh): %s", e)
