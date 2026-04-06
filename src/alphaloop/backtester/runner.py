"""
Backtest runner — manages background execution of backtest runs.

Picks up pending backtests, runs them via vectorbt (construction-parity), and streams logs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.backtester.vbt_engine import run_vectorbt_backtest
from alphaloop.core.types import TradeDirection
from alphaloop.db.repositories.backtest_repo import BacktestRepository
from alphaloop.trading.strategy_loader import (
    build_algorithmic_params,
    build_strategy_resolution_input,
    normalize_strategy_signal_logic,
    normalize_strategy_signal_rules,
    normalize_strategy_tools,
    serialize_strategy_spec,
    resolve_strategy_setup_family,
    resolve_strategy_signal_mode,
    resolve_strategy_source,
)

# Checkpoint directory
_CHECKPOINT_DIR = Path("checkpoints")

logger = logging.getLogger(__name__)

# ── In-memory state ─────────────────────────────────────────────────────────
_tasks: dict[str, asyncio.Task] = {}
_stop_flags: dict[str, bool] = {}
_logs: dict[str, list[str]] = defaultdict(list)
_synthetic_runs: set[str] = set()  # Track runs that fell back to synthetic data
_MAX_LOG_LINES = 500


def _log(run_id: str, msg: str, level: str = "INFO") -> None:
    """Append a timestamped, level-tagged line to the run's log buffer."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    prefix = f"[{level}] " if level != "INFO" else ""
    line = f"[{ts}] {prefix}{msg}"
    buf = _logs[run_id]
    buf.append(line)
    if len(buf) > _MAX_LOG_LINES:
        buf[:] = buf[-_MAX_LOG_LINES:]
    logger.info("[bt:%s] %s", run_id, msg)


def get_logs(run_id: str, offset: int = 0) -> list[str]:
    """Return log lines starting from offset."""
    return _logs.get(run_id, [])[offset:]


def is_running(run_id: str) -> bool:
    t = _tasks.get(run_id)
    return t is not None and not t.done()


def request_stop(run_id: str) -> bool:
    if run_id in _tasks and not _tasks[run_id].done():
        _stop_flags[run_id] = True
        return True
    return False


def delete_run_data(run_id: str) -> None:
    _logs.pop(run_id, None)
    _stop_flags.pop(run_id, None)
    t = _tasks.pop(run_id, None)
    if t and not t.done():
        t.cancel()
    # Clean up checkpoint file
    cp = _CHECKPOINT_DIR / f"{run_id}.json"
    if cp.exists():
        cp.unlink(missing_ok=True)


def _base_backtest_params(
    *,
    signal_mode: str,
    signal_rules: list[dict] | None,
    signal_logic: str,
    signal_auto: bool,
    tools: list[str] | None,
    setup_family: str = "",
    strategy_spec: dict[str, Any] | None = None,
    source: str = "backtest_runner",
) -> "BacktestParams":
    """Build baseline backtest params with spec-consistent strategy identity."""
    tool_flags = {str(name): True for name in (tools or [])}
    strategy_payload = build_strategy_resolution_input(
        {
            "signal_mode": signal_mode,
            "setup_family": setup_family,
            "strategy_spec": dict(strategy_spec or {}),
            "source": source,
            "tools": tool_flags,
        },
        signal_rules=signal_rules,
        signal_logic=signal_logic,
    )
    rules = normalize_strategy_signal_rules(
        strategy_payload["params"].get("signal_rules"),
        default_to_ema=(strategy_payload["params"].get("signal_rules") is None),
    )
    logic = normalize_strategy_signal_logic(strategy_payload["params"].get("signal_logic"))
    strategy_payload["params"]["signal_rules"] = rules
    strategy_payload["params"]["signal_logic"] = logic
    from alphaloop.backtester.params import BacktestParams

    return BacktestParams(
        signal_rules=rules,
        signal_logic=logic,
        signal_auto=signal_auto,
        signal_mode=resolve_strategy_signal_mode(strategy_payload),
        setup_family=resolve_strategy_setup_family(strategy_payload),
        strategy_spec=serialize_strategy_spec(strategy_payload),
        tools=tool_flags,
        source=resolve_strategy_source(strategy_payload),
    )


# ── Checkpoint save/load ──────────────────────────────────────────────────────

def _data_hash(closes: np.ndarray) -> str:
    """Compute a short hash of the data array for checkpoint validation."""
    return hashlib.sha256(closes.tobytes()).hexdigest()[:16]


def _save_checkpoint(
    run_id: str,
    generation: int,
    best_params: "BacktestParams",
    best_sharpe: float,
    data_hash: str,
) -> str:
    """Save checkpoint JSON after each generation. Returns the file path."""
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cp_path = _CHECKPOINT_DIR / f"{run_id}.json"
    normalized_signal_rules = normalize_strategy_signal_rules(
        best_params.signal_rules,
        default_to_ema=(best_params.signal_rules is None),
    )
    normalized_signal_logic = normalize_strategy_signal_logic(best_params.signal_logic)
    strategy_payload = build_strategy_resolution_input(
        {
            "signal_mode": best_params.signal_mode,
            "setup_family": best_params.setup_family,
            "strategy_spec": dict(best_params.strategy_spec or {}),
            "source": resolve_strategy_source(best_params),
            "tools": normalize_strategy_tools(best_params.tools),
        },
        signal_rules=normalized_signal_rules,
        signal_logic=normalized_signal_logic,
    )
    resolved_params = build_algorithmic_params(strategy_payload)
    payload = {
        "run_id": run_id,
        "generation": generation,
        "best_sharpe": best_sharpe,
        "data_hash": data_hash,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "best_params": {
            "ema_fast": best_params.ema_fast,
            "ema_slow": best_params.ema_slow,
            "sl_atr_mult": best_params.sl_atr_mult,
            "tp1_rr": best_params.tp1_rr,
            "tp2_rr": best_params.tp2_rr,
            "rsi_ob": best_params.rsi_ob,
            "rsi_os": best_params.rsi_os,
            "rsi_period": best_params.rsi_period,
            "risk_pct": best_params.risk_pct,
            "signal_rules": list(resolved_params.get("signal_rules") or []),
            "signal_logic": resolved_params.get("signal_logic") or "AND",
            "signal_auto": best_params.signal_auto,
            "max_param_change_pct": best_params.max_param_change_pct,
            "signal_mode": resolve_strategy_signal_mode(strategy_payload),
            "setup_family": resolve_strategy_setup_family(strategy_payload),
            "strategy_spec": serialize_strategy_spec(strategy_payload),
            "tools": normalize_strategy_tools(best_params.tools),
            "source": resolve_strategy_source(best_params),
        },
    }
    tmp = cp_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(cp_path)
    _log(run_id, f"Checkpoint saved: gen={generation}, sharpe={best_sharpe:.3f} → {cp_path}", "CKPT")
    return str(cp_path)


def _load_checkpoint(
    run_id: str, current_data_hash: str
) -> tuple["BacktestParams | None", int, float]:
    """
    Load checkpoint if it exists and data hash matches.

    Returns: (best_params or None, resume_generation, best_sharpe)
    If no valid checkpoint, returns (None, 0, -999.0).
    """
    cp_path = _CHECKPOINT_DIR / f"{run_id}.json"
    if not cp_path.exists():
        return None, 0, -999.0
    try:
        payload = json.loads(cp_path.read_text())
        if payload.get("data_hash") != current_data_hash:
            logger.warning(
                "Checkpoint data hash mismatch for %s — data window shifted, loading params anyway",
                run_id,
            )
            _log(run_id, "Data hash mismatch (yfinance window shift) — loading checkpoint params anyway", "WARN")
        from alphaloop.backtester.params import BacktestParams
        p = payload["best_params"]
        normalized_signal_rules = normalize_strategy_signal_rules(
            p.get("signal_rules"),
            default_to_ema=("signal_rules" not in p or p.get("signal_rules") is None),
        )
        normalized_signal_logic = normalize_strategy_signal_logic(p.get("signal_logic"))
        strategy_payload = build_strategy_resolution_input(
            {
                "signal_mode": p.get("signal_mode"),
                "setup_family": p.get("setup_family"),
                "strategy_spec": p.get("strategy_spec", {}) or {},
                "source": resolve_strategy_source(p),
                "tools": normalize_strategy_tools(p.get("tools", {}) or {}),
            },
            signal_rules=normalized_signal_rules,
            signal_logic=normalized_signal_logic,
        )
        resolved_params = build_algorithmic_params(strategy_payload)
        params = BacktestParams(
            ema_fast=p["ema_fast"],
            ema_slow=p["ema_slow"],
            sl_atr_mult=p["sl_atr_mult"],
            tp1_rr=p["tp1_rr"],
            tp2_rr=p["tp2_rr"],
            rsi_ob=p["rsi_ob"],
            rsi_os=p["rsi_os"],
            rsi_period=p.get("rsi_period", 14),
            risk_pct=p.get("risk_pct", 0.01),
            signal_rules=list(resolved_params.get("signal_rules") or []),
            signal_logic=resolved_params.get("signal_logic") or "AND",
            signal_auto=p.get("signal_auto", False),
            max_param_change_pct=p.get("max_param_change_pct", 0.15),
            signal_mode=resolve_strategy_signal_mode(strategy_payload),
            setup_family=resolve_strategy_setup_family(strategy_payload),
            strategy_spec=serialize_strategy_spec(strategy_payload),
            tools=normalize_strategy_tools(p.get("tools", {}) or {}),
            source=resolve_strategy_source(p),
        )
        gen = payload.get("generation", 0)
        sharpe = payload.get("best_sharpe", -999.0)
        logger.info("Loaded checkpoint for %s: gen=%d, sharpe=%.3f", run_id, gen, sharpe)
        _log(run_id, f"Checkpoint loaded: gen={gen}, sharpe={sharpe:.3f}", "CKPT")
        return params, gen, sharpe
    except Exception as exc:
        logger.warning("Failed to load checkpoint for %s: %s", run_id, exc)
        return None, 0, -999.0


# ── Signal function with tunable params ───────────────────────────────────────

from alphaloop.backtester.params import BacktestParams


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Fast EMA computation."""
    out = np.empty_like(arr)
    out[:period] = np.nan
    if len(arr) < period:
        return out
    out[period - 1] = np.mean(arr[:period])
    alpha = 2.0 / (period + 1)
    for i in range(period, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI computation."""
    out = np.full(len(closes), 50.0)
    if len(closes) < period + 1:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _ema200(arr: np.ndarray) -> float | None:
    """Get current EMA200 value or None if insufficient data."""
    if len(arr) < 200:
        return None
    e = _ema(arr, 200)
    return float(e[-1]) if not np.isnan(e[-1]) else None


def _detect_bos_simple(highs: np.ndarray, lows: np.ndarray, lookback: int = 20) -> str:
    """Simple BOS: bullish if new high broken, bearish if new low broken."""
    if len(highs) < lookback + 1:
        return "neutral"
    recent_high = np.max(highs[-lookback - 1:-1])
    recent_low = np.min(lows[-lookback - 1:-1])
    if highs[-1] > recent_high:
        return "bullish"
    if lows[-1] < recent_low:
        return "bearish"
    return "neutral"


def _has_fvg(highs: np.ndarray, lows: np.ndarray, direction: str, lookback: int = 20) -> bool:
    """Check if a fair value gap exists in the direction."""
    n = min(lookback, len(highs) - 2)
    for j in range(2, n):
        if direction == "BUY" and lows[-j] > highs[-j - 2]:
            return True  # bullish FVG
        if direction == "SELL" and highs[-j] < lows[-j - 2]:
            return True  # bearish FVG
    return False


def _ema_from_array(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA computed from a raw numpy array (for MACD signal line)."""
    out = np.full(len(arr), np.nan, dtype=float)
    if len(arr) == 0:
        return out
    finite_idx = np.where(np.isfinite(arr))[0]
    if len(finite_idx) == 0:
        return out
    start = int(finite_idx[0])
    mult = 2.0 / (period + 1)
    out[start] = arr[start]
    for j in range(start + 1, len(arr)):
        if not np.isfinite(arr[j]):
            continue
        out[j] = arr[j] * mult + out[j - 1] * (1 - mult)
    return out


def _adx_simple(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Simplified ADX for backtest (returns scalar, not full series)."""
    n = len(closes)
    if n < period * 2:
        return 25.0  # default to "trending" if insufficient data
    plus_dm = np.maximum(np.diff(highs), 0.0)
    minus_dm = np.maximum(-np.diff(lows), 0.0)
    # Only keep larger
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0
    minus_dm[~mask] = 0
    tr = np.maximum(highs[1:] - lows[1:], np.abs(highs[1:] - closes[:-1]))
    # Simple rolling average for speed
    smooth_tr = np.convolve(tr, np.ones(period) / period, mode='valid')
    smooth_plus = np.convolve(plus_dm, np.ones(period) / period, mode='valid')
    smooth_minus = np.convolve(minus_dm, np.ones(period) / period, mode='valid')
    if len(smooth_tr) == 0 or smooth_tr[-1] == 0:
        return 25.0
    plus_di = 100 * smooth_plus[-1] / smooth_tr[-1]
    minus_di = 100 * smooth_minus[-1] / smooth_tr[-1]
    denom = plus_di + minus_di
    if denom == 0:
        return 0.0
    dx = 100 * abs(plus_di - minus_di) / denom
    return float(dx)


def _swing_structure_simple(highs: np.ndarray, lows: np.ndarray, lookback: int = 5) -> str:
    """Simplified swing structure: bullish (HH+HL), bearish (LH+LL), or ranging."""
    n = len(highs)
    if n < lookback * 4:
        return "ranging"
    swing_highs = []
    swing_lows = []
    for idx in range(lookback, n - lookback):
        window_h = highs[idx - lookback:idx + lookback + 1]
        window_l = lows[idx - lookback:idx + lookback + 1]
        if highs[idx] == window_h.max():
            swing_highs.append(float(highs[idx]))
        if lows[idx] == window_l.min():
            swing_lows.append(float(lows[idx]))
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"
    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1] > swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1] < swing_lows[-2]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "ranging"


def _bollinger_pct_b(closes: np.ndarray, period: int, std_dev: float) -> np.ndarray:
    """Bollinger %B array: 0 = at lower band, 1 = at upper band."""
    out = np.full(len(closes), np.nan)
    for idx in range(period - 1, len(closes)):
        sl = closes[idx - period + 1:idx + 1]
        mid = float(np.mean(sl))
        std = float(np.std(sl))
        if std > 0:
            lower = mid - std_dev * std
            upper = mid + std_dev * std
            out[idx] = (closes[idx] - lower) / (upper - lower)
    return out


def _adx_arrays(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (adx, plus_di, minus_di) arrays using Wilder smoothing."""
    n = len(closes)
    adx_out = np.full(n, np.nan)
    plus_di_out = np.full(n, np.nan)
    minus_di_out = np.full(n, np.nan)
    if n < period * 2 + 2:
        return adx_out, plus_di_out, minus_di_out

    h_diff = np.diff(highs)
    l_diff = -np.diff(lows)
    pdm = np.where((h_diff > l_diff) & (h_diff > 0), h_diff, 0.0)
    ndm = np.where((l_diff > h_diff) & (l_diff > 0), l_diff, 0.0)
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1]))
    )

    sm_tr = np.empty(n - 1)
    sm_pdm = np.empty(n - 1)
    sm_ndm = np.empty(n - 1)
    sm_tr[period - 1] = np.sum(tr[:period])
    sm_pdm[period - 1] = np.sum(pdm[:period])
    sm_ndm[period - 1] = np.sum(ndm[:period])
    for j in range(period, n - 1):
        sm_tr[j] = sm_tr[j - 1] - sm_tr[j - 1] / period + tr[j]
        sm_pdm[j] = sm_pdm[j - 1] - sm_pdm[j - 1] / period + pdm[j]
        sm_ndm[j] = sm_ndm[j - 1] - sm_ndm[j - 1] / period + ndm[j]

    dx_arr = np.full(n - 1, np.nan)
    for j in range(period - 1, n - 1):
        if sm_tr[j] > 0:
            pd_val = 100.0 * sm_pdm[j] / sm_tr[j]
            nd_val = 100.0 * sm_ndm[j] / sm_tr[j]
            plus_di_out[j + 1] = pd_val
            minus_di_out[j + 1] = nd_val
            denom = pd_val + nd_val
            dx_arr[j] = 100.0 * abs(pd_val - nd_val) / denom if denom > 0 else 0.0

    # Smooth DX into ADX using Wilder averaging
    start = period * 2 - 1
    if start < n - 1:
        adx_out[start + 1] = float(np.nanmean(dx_arr[period - 1:start]))
        for j in range(start, n - 2):
            if not np.isnan(adx_out[j + 1]) and not np.isnan(dx_arr[j]):
                adx_out[j + 2] = (adx_out[j + 1] * (period - 1) + dx_arr[j]) / period

    return adx_out, plus_di_out, minus_di_out


def _rolling_swing_hi_lo(
    highs: np.ndarray, lows: np.ndarray, lookback: int = 20
) -> tuple[np.ndarray, np.ndarray]:
    """Rolling prior swing high/low arrays (max/min over last `lookback` bars, excluding current)."""
    n = len(highs)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    for idx in range(lookback, n):
        sh[idx] = float(np.max(highs[idx - lookback:idx]))
        sl[idx] = float(np.min(lows[idx - lookback:idx]))
    return sh, sl


def make_signal_fn(params: BacktestParams, filters: list[str]):
    """Create a signal function with the given tunable params and active filters.

    Indicators (EMA fast/slow, RSI, EMA200, MACD) are pre-computed once on the
    full price array and cached by array id — O(n) total instead of O(n²).
    """
    _cache: dict = {}
    strategy_payload = build_strategy_resolution_input(params, tools=params.tools)
    resolved_algo_params = build_algorithmic_params(strategy_payload)
    signal_rules = list(resolved_algo_params.get("signal_rules") or [{"source": "ema_crossover"}])
    signal_logic = resolved_algo_params.get("signal_logic") or "AND"
    signal_sources = {r.get("source") for r in signal_rules}

    async def signal_fn(
        i: int,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        _filters: list[str],
        timestamps: list | np.ndarray | None = None,
    ) -> tuple | None:
        warmup = max(params.ema_slow + 2, 60)
        if i < warmup:
            return None

        # Pre-compute full indicator arrays once per unique price array (O(n) total)
        cid = id(closes)
        if cid not in _cache:
            c: dict = {
                'ema_f': _ema(closes, params.ema_fast),
                'ema_s': _ema(closes, params.ema_slow),
                'rsi':   _rsi(closes, params.rsi_period),
            }
            if "ema200_filter" in filters:
                c['ema200'] = _ema(closes, 200)
            if "macd_filter" in filters or any(
                r.get("source") == "macd_crossover"
                for r in signal_rules
            ):
                mf = _ema(closes, params.macd_fast)
                ms = _ema(closes, params.macd_slow)
                ml = mf - ms
                c['macd_line'] = ml
                c['macd_sig']  = _ema_from_array(ml, params.macd_signal)
            if "bollinger_breakout" in signal_sources:
                c['bb_pct_b'] = _bollinger_pct_b(closes, params.bb_period, params.bb_std_dev)
            if "adx_trend" in signal_sources:
                c['adx_arr'], c['plus_di_arr'], c['minus_di_arr'] = _adx_arrays(
                    highs, lows, closes, params.adx_period
                )
            if "bos_confirm" in signal_sources:
                c['swing_h_arr'], c['swing_l_arr'] = _rolling_swing_hi_lo(highs, lows, lookback=20)
            _cache[cid] = c
        c = _cache[cid]

        ema_f = c['ema_f']
        ema_s = c['ema_s']

        if np.isnan(ema_f[i]) or np.isnan(ema_s[i]):
            return None

        # Phase 7J: Use next-bar open for entry to avoid look-ahead bias.
        # Signal is decided at bar i close, but entry is at bar i+1 open.
        if i + 1 >= len(opens):
            return None  # no next bar available for entry
        price   = opens[i + 1]
        rsi_val = c['rsi'][i]
        atr_period = min(14, i)
        h_slice = highs[i - atr_period:i + 1]
        l_slice = lows[i - atr_period:i + 1]
        c_prev = closes[i - atr_period - 1:i] if i > atr_period else closes[max(0, i - atr_period - 1):i]
        min_len = min(len(h_slice), len(l_slice), len(c_prev))
        h_slice = h_slice[-min_len:]
        l_slice = l_slice[-min_len:]
        c_prev  = c_prev[-min_len:]
        tr  = np.maximum(h_slice - l_slice, np.abs(h_slice - c_prev))
        atr = float(np.mean(tr)) if len(tr) > 0 else price * 0.01

        # --- Session Filter ---
        if "session_filter" in filters and hasattr(_filters, '__len__'):
            try:
                from alphaloop.utils.time import get_session_score_for_hour
                if timestamps is not None and i < len(timestamps):
                    ts = timestamps[i]
                    bar_hour = ts.hour if hasattr(ts, 'hour') else int(ts) % (24 * 3600) // 3600
                else:
                    bar_hour = 12
                if get_session_score_for_hour(bar_hour) < 0.50:
                    return None
            except (ImportError, Exception):
                pass

        # --- Volatility Filter ---
        if "volatility_filter" in filters:
            atr_pct = (atr / price) * 100 if price > 0 else 0
            if atr_pct > 2.5 or atr_pct < 0.05:
                return None

        # --- Signal source dispatcher ---
        from alphaloop.signals.conditions import (
            check_ema_crossover, check_macd_crossover, check_rsi_reversal,
            check_bollinger, check_adx_trend, check_bos, combine,
        )
        rule_results: list[tuple[bool, bool]] = []
        for rule in signal_rules:
            src = rule.get("source", "ema_crossover")
            if src == "ema_crossover":
                rule_results.append(check_ema_crossover(
                    float(ema_f[i]), float(ema_f[i - 1]),
                    float(ema_s[i]), float(ema_s[i - 1]),
                    rsi_val, params.rsi_ob, params.rsi_os,
                ))
            elif src == "macd_crossover" and "macd_line" in c:
                hist = c["macd_line"][i] - c["macd_sig"][i]
                hist_prev = c["macd_line"][i - 1] - c["macd_sig"][i - 1]
                if not (np.isnan(hist) or np.isnan(hist_prev)):
                    rule_results.append(check_macd_crossover(float(hist), float(hist_prev)))
            elif src == "rsi_reversal":
                rule_results.append(check_rsi_reversal(
                    rsi_val, float(c["rsi"][i - 1]), params.rsi_ob, params.rsi_os,
                ))
            elif src == "bollinger_breakout" and "bb_pct_b" in c:
                pb = c["bb_pct_b"][i]
                if not np.isnan(pb):
                    rule_results.append(check_bollinger(float(pb)))
            elif src == "adx_trend" and "adx_arr" in c:
                adx_v = c["adx_arr"][i]
                if not np.isnan(adx_v):
                    rule_results.append(check_adx_trend(
                        float(adx_v),
                        float(c["plus_di_arr"][i]),
                        float(c["minus_di_arr"][i]),
                        params.adx_min_threshold,
                    ))
            elif src == "bos_confirm" and "swing_h_arr" in c:
                sh = c["swing_h_arr"][i]
                sl_v = c["swing_l_arr"][i]
                rule_results.append(check_bos(
                    float(closes[i]),
                    None if np.isnan(sh) else float(sh),
                    None if np.isnan(sl_v) else float(sl_v),
                ))

        if not rule_results:
            return None

        is_bull, is_bear = combine(rule_results, signal_logic)
        if not is_bull and not is_bear:
            return None

        direction = "BUY" if is_bull else "SELL"

        # --- EMA200 Trend Filter ---
        if "ema200_filter" in filters:
            e200 = c['ema200'][i]
            if not np.isnan(e200):
                if direction == "BUY" and price < e200:
                    return None
                if direction == "SELL" and price > e200:
                    return None

        # --- BOS Guard (only needs last 21 bars) ---
        if "bos_guard" in filters:
            bos = _detect_bos_simple(highs[max(0, i - 21):i + 1], lows[max(0, i - 21):i + 1])
            if direction == "BUY" and bos != "bullish":
                return None
            if direction == "SELL" and bos != "bearish":
                return None

        # --- FVG Guard (only needs last 21 bars) ---
        if "fvg_guard" in filters:
            if not _has_fvg(highs[max(0, i - 21):i + 1], lows[max(0, i - 21):i + 1], direction):
                return None

        # --- Tick Jump Guard ---
        if "tick_jump_guard" in filters and i >= 2:
            move = abs(closes[i] - closes[i - 2])
            if atr > 0 and move / atr > 0.8:
                return None

        # --- Liquidity Vacuum Guard ---
        if "liq_vacuum_guard" in filters:
            bar_range = highs[i] - lows[i]
            body = abs(opens[i] - closes[i])
            if bar_range > 0 and atr > 0:
                if bar_range / atr > 2.5 and (body / bar_range) * 100 < 30:
                    return None

        # --- VWAP Guard ---
        if "vwap_guard" in filters:
            dist = abs(price - float(ema_f[i]))
            if atr > 0 and dist / atr > 1.5:
                return None

        # --- MACD Filter (pre-computed) ---
        if "macd_filter" in filters and i >= params.macd_slow + params.macd_signal:
            histogram = c['macd_line'][i] - c['macd_sig'][i]
            if direction == "BUY" and histogram < 0:
                return None
            if direction == "SELL" and histogram > 0:
                return None

        # --- Bollinger Filter (only needs bb_period bars) ---
        if "bollinger_filter" in filters and i >= params.bb_period:
            bb_slice = closes[i - params.bb_period + 1:i + 1]
            bb_mid = float(np.mean(bb_slice))
            bb_std = float(np.std(bb_slice))
            if bb_std > 0:
                pct_b = (price - (bb_mid - params.bb_std_dev * bb_std)) / (2 * params.bb_std_dev * bb_std)
                if direction == "BUY" and pct_b > 0.7:
                    return None
                if direction == "SELL" and pct_b < 0.3:
                    return None

        # --- ADX Filter (only needs adx_period*3 bars) ---
        if "adx_filter" in filters and i >= params.adx_period * 2:
            win = params.adx_period * 3
            adx_val = _adx_simple(highs[max(0, i - win):i + 1], lows[max(0, i - win):i + 1],
                                  closes[max(0, i - win):i + 1], params.adx_period)
            if adx_val < params.adx_min_threshold:
                return None

        # --- Volume Filter (no-op in backtest — no volume data) ---

        # --- Swing Structure Filter (only needs last 40 bars) ---
        if "swing_structure" in filters and i >= 20:
            swing = _swing_structure_simple(highs[max(0, i - 40):i + 1], lows[max(0, i - 40):i + 1])
            if direction == "BUY" and swing != "bullish":
                return None
            if direction == "SELL" and swing != "bearish":
                return None

        # Build trade with tunable SL/TP multipliers
        sl_dist  = params.sl_atr_mult * atr
        tp1_dist = sl_dist * params.tp1_rr
        tp2_dist = sl_dist * params.tp2_rr

        setup_label = "_".join(r.get("source", "ema_crossover") for r in signal_rules)
        if direction == "BUY":
            return (TradeDirection.BUY, price, price - sl_dist, price + tp1_dist, price + tp2_dist, setup_label, 0.75)
        else:
            return (TradeDirection.SELL, price, price + sl_dist, price - tp1_dist, price - tp2_dist, setup_label, 0.75)

    return signal_fn


async def _run_engine_in_thread(**kwargs):
    """Legacy stub — kept to avoid NameError if referenced elsewhere."""
    raise NotImplementedError("_run_engine_in_thread removed; use _run_vbt via asyncio.to_thread")


def _run_vbt(
    symbol: str,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timestamps: list | None,
    balance: float,
    params: "BacktestParams",
    **_kwargs,  # absorb legacy kwargs (signal_fn, run_id, stop_check, filters)
):
    """Run vectorbt backtest from numpy arrays using TradeConstructor (construction-parity)."""
    import pandas as pd
    raw_params = params.model_dump() if hasattr(params, "model_dump") else vars(params)
    strategy_payload = build_strategy_resolution_input(
        {
            "signal_mode": getattr(params, "signal_mode", raw_params.get("signal_mode")),
            "setup_family": getattr(params, "setup_family", raw_params.get("setup_family")),
            "strategy_spec": dict(getattr(params, "strategy_spec", raw_params.get("strategy_spec", {})) or {}),
            "source": resolve_strategy_source(params),
            "tools": normalize_strategy_tools(getattr(params, "tools", raw_params.get("tools", {}))),
        },
        signal_rules=getattr(params, "signal_rules", raw_params.get("signal_rules")),
        signal_logic=getattr(params, "signal_logic", raw_params.get("signal_logic")),
    )
    resolved_algo_params = build_algorithmic_params(strategy_payload)
    canonical_params = dict(raw_params)
    canonical_params.update({
        "signal_mode": resolve_strategy_signal_mode(strategy_payload),
        "setup_family": resolve_strategy_setup_family(strategy_payload),
        "source": resolve_strategy_source(strategy_payload),
        "tools": normalize_strategy_tools(getattr(params, "tools", raw_params.get("tools", {}))),
        "strategy_spec": serialize_strategy_spec(strategy_payload),
        "signal_rules": list(resolved_algo_params.get("signal_rules") or []),
        "signal_logic": resolved_algo_params.get("signal_logic") or "AND",
    })
    df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                        "volume": np.ones(len(closes), dtype=float)})
    if timestamps:
        df["time"] = pd.Series(timestamps)
    return run_vectorbt_backtest(
        df,
        canonical_params,
        symbol=symbol,
        balance=balance,
        risk_pct=params.risk_pct,
    )


def _strategy_version_write_kwargs(
    *,
    params: "BacktestParams",
    metrics: dict[str, Any],
    tools: list[str] | dict[str, bool] | None,
    source: str,
    name: str,
    timeframe: str,
    days: int,
    initial_capital: float,
) -> dict[str, Any]:
    """Build spec-first kwargs for create_strategy_version from final best params."""
    normalized_tools = normalize_strategy_tools(getattr(params, "tools", tools) or tools or {})
    return {
        "params": params,
        "metrics": metrics,
        "tools": [tool for tool, enabled in normalized_tools.items() if enabled],
        "source": resolve_strategy_source(params) or str(source or ""),
        "name": name,
        "timeframe": timeframe,
        "days": days,
        "initial_capital": initial_capital,
        "signal_mode": resolve_strategy_signal_mode(params),
    }


# ── Main runner ──────────────────────────────────────────────────────────────

async def start_backtest(
    run_id: str,
    symbol: str,
    days: int,
    balance: float,
    max_generations: int,
    session_factory: async_sessionmaker,
    timeframe: str = "1h",
    tools: list[str] | None = None,
    name: str = "",
    signal_mode: str = "algo_ai",
    signal_rules: list[dict] | None = None,
    signal_logic: str = "AND",
    signal_auto: bool = False,
    setup_family: str = "",
    strategy_spec: dict[str, Any] | None = None,
    source: str = "backtest_runner",
) -> None:
    """Spawn a background task to run the backtest."""
    if run_id in _tasks and not _tasks[run_id].done():
        return

    _stop_flags[run_id] = False
    if _logs.get(run_id):  # resuming — preserve history, add separator
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        _logs[run_id].append(f"[{ts}] [INFO] ─── Resumed ───")
    else:
        _logs[run_id] = []

    task = asyncio.create_task(
        _run_backtest(run_id, symbol, days, balance, max_generations,
                      session_factory, timeframe, tools or [], name=name, signal_mode=signal_mode,
                      signal_rules=signal_rules, signal_logic=signal_logic, signal_auto=signal_auto,
                      setup_family=setup_family, strategy_spec=strategy_spec, source=source)
    )
    _tasks[run_id] = task


async def _run_backtest(
    run_id: str,
    symbol: str,
    days: int,
    balance: float,
    max_generations: int,
    session_factory: async_sessionmaker,
    timeframe: str = "1h",
    tools: list[str] | None = None,
    name: str = "",
    signal_mode: str = "algo_ai",
    signal_rules: list[dict] | None = None,
    signal_logic: str = "AND",
    signal_auto: bool = False,
    setup_family: str = "",
    strategy_spec: dict[str, Any] | None = None,
    source: str = "backtest_runner",
) -> None:
    """Background coroutine that runs one backtest."""
    tools = tools or []
    _log(
        run_id,
        f"Starting backtest: {symbol}, {days}d, ${balance:.0f}, {max_generations} gens, TF={timeframe}, mode={signal_mode}",
    )
    if tools:
        _log(run_id, f"Active tools: {', '.join(tools)}")

    # Update state to running
    async with session_factory() as session:
        repo = BacktestRepository(session)
        await repo.update_state(run_id, "running", pid=os.getpid(),
                                started_at=datetime.now(timezone.utc))
        await session.commit()

    try:
        # Fetch OHLCV data
        _log(run_id, f"Fetching {days} days of {symbol} data ({timeframe}) — MT5 primary, yfinance fallback...", "DATA")
        opens, highs, lows, closes, timestamps = await _fetch_data(symbol, days, run_id, timeframe)
        is_synthetic = run_id in _synthetic_runs
        _log(run_id, f"Loaded {len(closes)} bars" + (" [SYNTHETIC DATA]" if is_synthetic else ""), "DATA")

        # Peek at checkpoint BEFORE updating DB so the generation counter
        # is not reset to 0 when resuming a paused run.
        d_hash = _data_hash(closes)
        _, cp_gen_peek, _ = _load_checkpoint(run_id, d_hash)

        async with session_factory() as session:
            repo = BacktestRepository(session)
            await repo.update_progress(
                run_id,
                generation=cp_gen_peek if cp_gen_peek > 0 else 0,
                phase="data_loaded",
                message=f"{len(closes)} bars loaded",
            )
            run = await repo.get_by_run_id(run_id)
            if run:
                run.bars_loaded = len(closes)
            await session.commit()

        from alphaloop.backtester.optimizer import (
            optimize, split_data, MIN_SHARPE_IMPROVEMENT, OVERFIT_GAP_THRESHOLD, MIN_TRADES,
        )

        base_params = _base_backtest_params(
            signal_mode=signal_mode,
            signal_rules=signal_rules,
            signal_logic=signal_logic,
            signal_auto=signal_auto,
            tools=tools,
            setup_family=setup_family,
            strategy_spec=strategy_spec,
            source=source,
        )
        best_params = base_params
        best_sharpe = -999.0
        best_result = None
        no_improve_count = 0

        # Split data for train/validation
        train_data, val_data = split_data(opens, highs, lows, closes, timestamps)
        _log(run_id, f"Split: {len(train_data['closes'])} train bars, {len(val_data['closes'])} val bars", "DATA")

        # ── Check for checkpoint (resume support) ────────────────────────
        # d_hash already computed above; _load_checkpoint is idempotent (file read)
        cp_params, cp_gen, cp_sharpe = _load_checkpoint(run_id, d_hash)
        resume_from_gen = 0
        if cp_params is not None and cp_gen > 0:
            best_params = cp_params
            best_sharpe = cp_sharpe
            resume_from_gen = cp_gen
            _log(run_id, f"Resuming from checkpoint: gen={cp_gen}, sharpe={cp_sharpe:.3f}", "CKPT")
            # Run on full data with restored params to get best_result
            best_result = await asyncio.to_thread(_run_vbt, symbol=symbol, opens=opens, highs=highs,
                lows=lows, closes=closes, timestamps=timestamps, balance=balance, params=best_params)
            _log(run_id, f"Restored result: {best_result.trade_count} trades, "
                         f"Sharpe={best_result.sharpe or '—'}", "STAT")

        if resume_from_gen == 0:
            # ── Gen 1: Baseline ──────────────────────────────────────────
            _log(run_id, f"=== Generation 1/{max_generations}: Baseline ===", "GEN")
            t0 = time.time()
            result = await asyncio.to_thread(_run_vbt, symbol=symbol, opens=opens, highs=highs,
                lows=lows, closes=closes, timestamps=timestamps, balance=balance, params=base_params)
            elapsed = time.time() - t0
            baseline_sharpe = result.sharpe or -999.0
            best_sharpe = baseline_sharpe
            best_result = result
            _log(run_id, f"Baseline: {result.trade_count} trades, WR={result.win_rate:.1%}, "
                         f"Sharpe={result.sharpe or '—'}, PnL=${result.total_pnl:.2f}, "
                         f"DD={result.max_drawdown_pct:.1f}% ({elapsed:.1f}s)", "STAT")
            if result.trade_count < MIN_TRADES:
                _log(run_id, f"⚠ Only {result.trade_count} trades — below minimum {MIN_TRADES}. "
                             f"Try fewer filters, a shorter timeframe, or more history days.", "WARN")
            _log(run_id, f"Params: EMA={base_params.ema_fast}/{base_params.ema_slow}, "
                         f"SL={base_params.sl_atr_mult}, TP1={base_params.tp1_rr}, TP2={base_params.tp2_rr}, "
                         f"RSI={base_params.rsi_os}-{base_params.rsi_ob}", "STAT")

            # Save checkpoint after baseline
            cp_path = _save_checkpoint(run_id, 1, best_params, best_sharpe, d_hash)
            async with session_factory() as session:
                repo = BacktestRepository(session)
                await repo.update_progress(
                    run_id, generation=1, phase="baseline",
                    message=f"Baseline: WR={result.win_rate:.1%} Sharpe={result.sharpe or '—'}",
                    best_sharpe=best_sharpe if best_sharpe > -999 else None,
                    best_wr=result.win_rate, best_pnl=result.total_pnl,
                    best_dd=result.max_drawdown_pct,
                    best_trades=result.trade_count,
                )
                run_obj = await repo.get_by_run_id(run_id)
                if run_obj:
                    run_obj.checkpoint_path = cp_path
                await session.commit()

        # ── Gen 2+: Optuna optimization ──────────────────────────────────
        start_gen = max(2, resume_from_gen + 1)
        for gen in range(start_gen, max_generations + 1):
            if _stop_flags.get(run_id, False):
                _log(run_id, f"Stop at gen boundary — checkpoint gen {gen - 1}", "CKPT")
                _save_checkpoint(run_id, gen - 1, best_params, best_sharpe, d_hash)
                async with session_factory() as session:
                    repo = BacktestRepository(session)
                    await repo.update_progress(
                        run_id, generation=gen - 1, phase="paused",
                        message=f"Stopped at generation {gen - 1}",
                        best_sharpe=best_sharpe if best_sharpe > -999 else None,
                        best_wr=best_result.win_rate if best_result else None,
                        best_pnl=best_result.total_pnl if best_result else None,
                        best_dd=best_result.max_drawdown_pct if best_result else None,
                        best_trades=best_result.trade_count if best_result else None,
                    )
                    await repo.update_state(run_id, "paused",
                                            message=f"Stopped at generation {gen - 1}")
                    await session.commit()
                return

            _log(run_id, f"=== Generation {gen}/{max_generations}: Optimizing ===", "GEN")

            # Run Optuna + backtests entirely in a thread to avoid blocking the event loop.
            def run_on_train(params: BacktestParams) -> float:
                """Sync: run vbt backtest on train split (runs in Optuna's thread)."""
                try:
                    r = _run_vbt(
                        symbol=symbol,
                        opens=train_data["opens"], highs=train_data["highs"],
                        lows=train_data["lows"], closes=train_data["closes"],
                        timestamps=train_data["timestamps"],
                        balance=balance, params=params,
                    )
                    if r.error:
                        return -999.0
                    if r.trade_count < MIN_TRADES:
                        return -999.0
                    return r.sharpe or -999.0
                except Exception:
                    return -999.0

            t0 = time.time()
            # Run entire optimization in thread pool — keeps event loop free for HTTP
            opt_params, train_sharpe, was_stopped = await asyncio.to_thread(
                optimize,
                best_params,
                run_on_train,
                30,  # n_trials
                lambda: _stop_flags.get(run_id, False),
                lambda msg: _log(run_id, msg),
            )
            elapsed = time.time() - t0

            if was_stopped:
                _log(run_id, f"Stop mid-optimization — checkpoint gen {gen - 1}", "CKPT")
                _save_checkpoint(run_id, gen - 1, best_params, best_sharpe, d_hash)
                async with session_factory() as session:
                    repo = BacktestRepository(session)
                    await repo.update_progress(
                        run_id, generation=gen - 1, phase="paused",
                        message=f"Stopped during gen {gen} — checkpoint at gen {gen - 1}",
                        best_sharpe=best_sharpe if best_sharpe > -999 else None,
                        best_wr=best_result.win_rate if best_result else None,
                        best_pnl=best_result.total_pnl if best_result else None,
                        best_dd=best_result.max_drawdown_pct if best_result else None,
                        best_trades=best_result.trade_count if best_result else None,
                    )
                    await repo.update_state(run_id, "paused",
                                            message=f"Stopped during gen {gen} — checkpoint at gen {gen - 1}")
                    await session.commit()
                return

            if opt_params is None or train_sharpe <= best_sharpe + MIN_SHARPE_IMPROVEMENT:
                no_improve_count += 1
                _log(run_id, f"Gen {gen}: No improvement (train Sharpe={train_sharpe:.3f} vs best={best_sharpe:.3f}). "
                             f"No-improve streak: {no_improve_count}", "STAT")
                if no_improve_count >= 3:
                    _log(run_id, "Early stop — no improvement for 3 consecutive generations", "WARN")
                    break
            else:
                # Validate on holdout
                _log(run_id, f"Gen {gen}: Train Sharpe={train_sharpe:.3f} — validating on holdout...", "STAT")
                val_result = await asyncio.to_thread(_run_vbt, symbol=symbol,
                    opens=val_data["opens"], highs=val_data["highs"],
                    lows=val_data["lows"], closes=val_data["closes"],
                    timestamps=val_data["timestamps"], balance=balance, params=opt_params)
                val_trades = val_result.trade_count
                val_sharpe = val_result.sharpe or -999.0

                # Skip overfit check if val set has too few trades — not enough data to judge
                if val_trades < MIN_TRADES:
                    _log(run_id, f"  Validation: only {val_trades} trades — skipping overfit check, proceeding to full-data confirm", "WARN")
                    gap = 0.0
                else:
                    gap = train_sharpe - val_sharpe
                    _log(run_id, f"  Validation: Sharpe={val_sharpe:.3f}, gap={gap:.3f} ({val_trades} trades)", "STAT")

                if gap > OVERFIT_GAP_THRESHOLD:
                    _log(run_id, f"  OVERFIT DETECTED (gap {gap:.3f} > {OVERFIT_GAP_THRESHOLD}) — skipping", "WARN")
                    no_improve_count += 1
                else:
                    # Run on full data to confirm
                    full_result = await asyncio.to_thread(_run_vbt, symbol=symbol,
                        opens=opens, highs=highs, lows=lows, closes=closes,
                        timestamps=timestamps, balance=balance, params=opt_params)
                    full_sharpe = full_result.sharpe or -999.0

                    if full_sharpe > best_sharpe + MIN_SHARPE_IMPROVEMENT:
                        best_sharpe = full_sharpe
                        best_params = opt_params
                        best_result = full_result
                        no_improve_count = 0
                        _log(run_id, f"  ACCEPTED: {full_result.trade_count} trades, WR={full_result.win_rate:.1%}, "
                                     f"Sharpe={full_result.sharpe or '—'}, PnL=${full_result.total_pnl:.2f}", "STAT")
                        _log(run_id, f"  Params: EMA={opt_params.ema_fast}/{opt_params.ema_slow}, "
                                     f"SL={opt_params.sl_atr_mult}, TP1={opt_params.tp1_rr}, "
                                     f"TP2={opt_params.tp2_rr}, RSI={opt_params.rsi_os}-{opt_params.rsi_ob}", "STAT")
                    else:
                        no_improve_count += 1
                        _log(run_id, f"  Full-data Sharpe={full_sharpe:.3f} — not enough improvement", "STAT")

            _log(run_id, f"Gen {gen} done ({elapsed:.1f}s)", "GEN")

            # Save checkpoint after each generation
            cp_path = _save_checkpoint(run_id, gen, best_params, best_sharpe, d_hash)
            async with session_factory() as session:
                repo = BacktestRepository(session)
                await repo.update_progress(
                    run_id, generation=gen, phase="optimizing",
                    message=f"Gen {gen}: best Sharpe={best_sharpe:.3f}" if best_sharpe > -999 else f"Gen {gen}",
                    best_sharpe=best_sharpe if best_sharpe > -999 else None,
                    best_wr=best_result.win_rate if best_result else None,
                    best_pnl=best_result.total_pnl if best_result else None,
                    best_dd=best_result.max_drawdown_pct if best_result else None,
                    best_trades=best_result.trade_count if best_result else None,
                )
                run_obj = await repo.get_by_run_id(run_id)
                if run_obj:
                    run_obj.checkpoint_path = cp_path
                await session.commit()

        # Completed
        _log(run_id, "=" * 50, "GEN")
        _log(run_id, "Backtest completed!", "GEN")
        if best_result:
            _log(run_id, f"Best: {best_result.trade_count} trades, WR={best_result.win_rate:.1%}, "
                         f"Sharpe={best_result.sharpe or '—'}, PnL=${best_result.total_pnl:.2f}", "STAT")
            _log(run_id, f"Best params: EMA={best_params.ema_fast}/{best_params.ema_slow}, "
                         f"SL={best_params.sl_atr_mult}, TP1={best_params.tp1_rr}, "
                         f"TP2={best_params.tp2_rr}, RSI={best_params.rsi_os}-{best_params.rsi_ob}", "STAT")

            # Auto-create strategy version file from best result
            try:
                from alphaloop.backtester.asset_trainer import create_strategy_version
                from alphaloop.backtester.deployment_pipeline import DEFAULT_GATES
                from alphaloop.core.types import StrategyStatus
                metrics = {
                    "total_trades": best_result.trade_count,
                    "win_rate": best_result.win_rate or 0,
                    "sharpe": best_result.sharpe or 0,
                    "max_drawdown_pct": best_result.max_drawdown_pct or 0,
                    "total_pnl": best_result.total_pnl or 0,
                }

                # Check if it meets the candidate→dry_run gate; retire immediately if not
                gate = next((g for g in DEFAULT_GATES if g.from_status == StrategyStatus.CANDIDATE), None)
                fails = []
                if gate:
                    if metrics["total_trades"] < gate.min_trades:
                        fails.append(f"trades {metrics['total_trades']} < {gate.min_trades}")
                    if gate.min_sharpe is not None and metrics["sharpe"] < gate.min_sharpe:
                        fails.append(f"sharpe {metrics['sharpe']:.2f} < {gate.min_sharpe}")
                    if gate.min_win_rate is not None and metrics["win_rate"] < gate.min_win_rate:
                        fails.append(f"WR {metrics['win_rate']:.1%} < {gate.min_win_rate:.1%}")
                    if gate.max_drawdown_pct is not None and metrics["max_drawdown_pct"] < gate.max_drawdown_pct:
                        fails.append(f"DD {metrics['max_drawdown_pct']:.1f}% < {gate.max_drawdown_pct:.1f}%")

                initial_status = "retired" if fails else "candidate"
                if fails:
                    _log(run_id, f"⚠ Strategy below promotion threshold — auto-retiring ({', '.join(fails)})", "WARN")

                version_kwargs = _strategy_version_write_kwargs(
                    params=best_params,
                    metrics=metrics,
                    tools=tools,
                    source="backtest_runner",
                    name=name or run_id,
                    timeframe=timeframe,
                    days=days,
                    initial_capital=balance,
                )
                version_data = create_strategy_version(
                    symbol=symbol,
                    status=initial_status,
                    **version_kwargs,
                )
                _log(run_id, f"Strategy version created: {symbol} v{version_data['_version']} [{initial_status}]")
            except Exception as ve:
                _log(run_id, f"Could not create strategy version: {ve}", "WARN")
                logger.warning("Strategy version creation failed: %s", ve, exc_info=True)

        async with session_factory() as session:
            repo = BacktestRepository(session)
            await repo.update_state(
                run_id, "completed",
                finished_at=datetime.now(timezone.utc),
                message="Completed successfully",
            )
            await session.commit()

    except Exception as exc:
        _log(run_id, f"FATAL: {exc}", "ERR")
        logger.exception("Backtest %s failed", run_id)
        async with session_factory() as session:
            repo = BacktestRepository(session)
            await repo.update_state(
                run_id, "failed",
                error_message=str(exc),
                finished_at=datetime.now(timezone.utc),
            )
            await session.commit()


async def _fetch_data(
    symbol: str, days: int, run_id: str, timeframe: str = "1h",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[datetime]]:
    """
    Fetch OHLCV data for backtesting.

    Priority: MT5 (no day limits) → yfinance (fallback) → synthetic (last resort).
    """
    # ── Try MT5 first (no day/timeframe limits) ──────────────────────────────
    result = await _fetch_data_mt5(symbol, days, run_id, timeframe)
    if result is not None:
        return result

    # ── Fallback to yfinance ──────────────────────────────────────────────────
    result = await _fetch_data_yfinance(symbol, days, run_id, timeframe)
    if result is not None:
        return result

    # ── Last resort: synthetic data ───────────────────────────────────────────
    _log(run_id, "⚠ All data sources failed — using SYNTHETIC random walk data. Results are NOT meaningful.", "WARN")
    _synthetic_runs.add(run_id)
    return _synthetic_data(days)


async def _fetch_data_mt5(
    symbol: str, days: int, run_id: str, timeframe: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[datetime]] | None:
    """Try to fetch OHLCV from MT5. Returns None if MT5 unavailable."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None

    # Hard cap: MT5 copy_rates_range tops out at ~100k bars per call
    _MT5_MAX_DAYS: dict[str, int] = {
        "1m": 69, "5m": 347, "15m": 1041, "30m": 2083,
        "1h": 4166, "4h": 16666, "1d": 99999, "1wk": 99999, "1mo": 99999,
    }
    max_days = _MT5_MAX_DAYS.get(timeframe, 365)
    if days > max_days:
        _log(run_id, f"MT5: capping {days}d → {max_days}d for {timeframe} (100k bar limit)", "WARN")
        days = max_days

    _log(run_id, f"Trying MT5 for {symbol} ({days}d, {timeframe})...", "DATA")

    tf_map = {
        "1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15, "30m": mt5.TIMEFRAME_M30,
        "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4,
        "1d": mt5.TIMEFRAME_D1, "1wk": mt5.TIMEFRAME_W1,
        "1mo": mt5.TIMEFRAME_MN1,
    }
    mt5_tf = tf_map.get(timeframe)
    if mt5_tf is None:
        _log(run_id, f"MT5: unsupported timeframe '{timeframe}'", "WARN")
        return None

    def _mt5_fetch():
        from datetime import timedelta
        # Auto-connect: try stored broker credentials, then bare init
        init_ok = False
        try:
            from alphaloop.core.config import AppConfig
            cfg = AppConfig()
            broker = cfg.broker
            kwargs = {}
            if broker.server:
                kwargs["server"] = broker.server
            if broker.login:
                kwargs["login"] = broker.login
            if broker.password.get_secret_value():
                kwargs["password"] = broker.password.get_secret_value()
            if broker.terminal_path:
                kwargs["path"] = broker.terminal_path
            if kwargs:
                init_ok = mt5.initialize(**kwargs)
        except Exception:
            pass
        if not init_ok:
            # Fallback: bare init (uses default MT5 terminal)
            init_ok = mt5.initialize()
        if not init_ok:
            _log(run_id, f"MT5 init failed: {mt5.last_error()}", "WARN")
            return None
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=days)
        # Try the symbol as-is first, then with 'm' suffix (broker convention)
        tried = []
        for sym in [symbol, symbol + "m", symbol.rstrip("m")]:
            if sym in tried:
                continue
            tried.append(sym)
            rates = mt5.copy_rates_range(sym, mt5_tf, date_from, date_to)
            if rates is not None and len(rates) > 0:
                return rates
            _log(run_id, f"MT5: copy_rates_range({sym!r}) returned None — {mt5.last_error()}", "WARN")
        return None

    try:
        rates = await asyncio.to_thread(_mt5_fetch)
    except Exception as e:
        _log(run_id, f"MT5 failed: {e}", "WARN")
        return None

    if rates is None or len(rates) == 0:
        _log(run_id, "MT5: no data returned — falling back to yfinance", "WARN")
        return None

    import pandas as pd
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)

    opens = df["open"].values.astype(np.float64)
    highs = df["high"].values.astype(np.float64)
    lows = df["low"].values.astype(np.float64)
    closes = df["close"].values.astype(np.float64)
    timestamps = [dt.to_pydatetime() for dt in df["time"]]

    _log(run_id, f"Loaded {len(closes)} bars from MT5 ({timestamps[0].date()} to {timestamps[-1].date()})", "DATA")

    if len(timestamps) >= 2:
        actual_days = (timestamps[-1] - timestamps[0]).days
        if actual_days > days * 1.1:
            _log(
                run_id,
                f"MT5 date range mismatch: requested {days}d but got {actual_days}d "
                f"({timestamps[0].date()} to {timestamps[-1].date()}) — terminal history may be incomplete.",
                "WARN",
            )

    return opens, highs, lows, closes, timestamps


async def _fetch_data_yfinance(
    symbol: str, days: int, run_id: str, timeframe: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[datetime]] | None:
    """Try to fetch OHLCV from yfinance. Returns None if unavailable."""
    try:
        import yfinance as yf
    except ImportError:
        _log(run_id, "yfinance not installed", "WARN")
        return None

    # Map broker symbols to yfinance tickers using the catalog
    try:
        from alphaloop.data.yf_catalog import get_yf_ticker
        ticker = get_yf_ticker(symbol)
    except ImportError:
        _fallback_map = {
            "XAUUSD": "GC=F", "XAUUSDm": "GC=F",
            "BTCUSD": "BTC-USD", "BTCUSDm": "BTC-USD",
            "ETHUSD": "ETH-USD", "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X",
        }
        ticker = _fallback_map.get(symbol, symbol)

    # yfinance interval limits: 1m=7d, 5m/15m/30m=60d, 1h=730d, 1d/1wk/1mo=unlimited
    YF_MAX_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730, "1d": 9999, "1wk": 9999, "1mo": 9999}
    max_days = YF_MAX_DAYS.get(timeframe, 730)
    actual_days = min(days, max_days)
    if actual_days < days:
        _log(run_id, f"yfinance caps {timeframe} to {max_days}d — fetching {actual_days}d", "WARN")

    _log(run_id, f"Downloading {ticker} from yfinance ({actual_days}d, {timeframe})...", "DATA")

    try:
        data = await asyncio.to_thread(
            lambda: yf.download(ticker, period=f"{actual_days}d", interval=timeframe, progress=False)
        )
    except Exception as e:
        _log(run_id, f"yfinance failed: {e}", "WARN")
        return None

    if data.empty:
        _log(run_id, f"No data from yfinance for {ticker}", "WARN")
        return None

    # Handle MultiIndex columns from yfinance
    if hasattr(data.columns, 'levels') and len(data.columns.levels) > 1:
        data = data.droplevel(1, axis=1)

    opens = data["Open"].values.astype(np.float64)
    highs = data["High"].values.astype(np.float64)
    lows = data["Low"].values.astype(np.float64)
    closes = data["Close"].values.astype(np.float64)
    timestamps = [dt.to_pydatetime().replace(tzinfo=timezone.utc) for dt in data.index]

    _log(run_id, f"Got {len(closes)} bars from yfinance ({timestamps[0].date()} to {timestamps[-1].date()})", "DATA")
    return opens, highs, lows, closes, timestamps


def _synthetic_data(days: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[datetime]]:
    """Generate synthetic OHLCV for testing when no data source available."""
    bars = days * 24
    np.random.seed(42)
    price = 2000.0
    opens = np.empty(bars)
    highs = np.empty(bars)
    lows = np.empty(bars)
    closes = np.empty(bars)
    timestamps = []
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(bars):
        o = price
        change = np.random.normal(0, price * 0.003)
        c = o + change
        h = max(o, c) + abs(np.random.normal(0, price * 0.001))
        l = min(o, c) - abs(np.random.normal(0, price * 0.001))
        opens[i], highs[i], lows[i], closes[i] = o, h, l, c
        price = c
        from datetime import timedelta
        timestamps.append(base + timedelta(hours=i))
    return opens, highs, lows, closes, timestamps
