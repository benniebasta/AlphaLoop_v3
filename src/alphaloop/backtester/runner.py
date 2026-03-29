"""
Backtest runner — manages background execution of backtest runs.

Picks up pending backtests, runs them via BacktestEngine, and streams logs.
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

from alphaloop.backtester.engine import BacktestEngine
from alphaloop.core.types import TradeDirection
from alphaloop.db.repositories.backtest_repo import BacktestRepository

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
            "max_param_change_pct": best_params.max_param_change_pct,
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
            max_param_change_pct=p.get("max_param_change_pct", 0.15),
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
    out = np.empty_like(arr, dtype=float)
    if len(arr) == 0:
        return out
    mult = 2.0 / (period + 1)
    out[0] = arr[0]
    for j in range(1, len(arr)):
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


def make_signal_fn(params: BacktestParams, filters: list[str]):
    """Create a signal function with the given tunable params and active filters."""

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

        sl_data = closes[:i + 1]
        ema_fast = _ema(sl_data, params.ema_fast)
        ema_slow = _ema(sl_data, params.ema_slow)
        rsi_arr = _rsi(sl_data, params.rsi_period)

        if np.isnan(ema_fast[-1]) or np.isnan(ema_slow[-1]):
            return None

        price = closes[i]
        rsi_val = rsi_arr[-1]
        atr_period = min(14, i)
        h_slice = highs[i - atr_period:i + 1]
        l_slice = lows[i - atr_period:i + 1]
        c_prev = closes[i - atr_period - 1:i] if i > atr_period else closes[max(0, i - atr_period - 1):i]
        # Align c_prev length with h_slice/l_slice
        min_len = min(len(h_slice), len(l_slice), len(c_prev))
        h_slice = h_slice[-min_len:]
        l_slice = l_slice[-min_len:]
        c_prev = c_prev[-min_len:]
        tr = np.maximum(h_slice - l_slice, np.abs(h_slice - c_prev))
        atr = float(np.mean(tr)) if len(tr) > 0 else price * 0.01

        # --- Session Filter (backtest-compatible: checks bar timestamp) ---
        if "session_filter" in filters and hasattr(_filters, '__len__'):
            try:
                from alphaloop.utils.time import get_session_score_for_hour
                if timestamps is not None and i < len(timestamps):
                    # Use actual bar timestamp to derive UTC hour
                    ts = timestamps[i]
                    if hasattr(ts, 'hour'):
                        bar_hour = ts.hour  # datetime object
                    else:
                        bar_hour = int(ts) % (24 * 3600) // 3600  # unix timestamp
                else:
                    # No timestamps — skip session filter rather than guess
                    bar_hour = 12  # Assume overlap session (always passes)
                session_score = get_session_score_for_hour(bar_hour)
                if session_score < 0.50:
                    return None
            except (ImportError, Exception):
                pass  # fail-open if util not available

        # --- Volatility Filter ---
        if "volatility_filter" in filters:
            atr_pct = (atr / price) * 100 if price > 0 else 0
            if atr_pct > 2.5 or atr_pct < 0.05:
                return None

        # Detect crossover with tunable RSI thresholds
        is_cross_up = (ema_fast[-1] > ema_slow[-1] and ema_fast[-2] <= ema_slow[-2]
                       and rsi_val < params.rsi_ob)
        is_cross_down = (ema_fast[-1] < ema_slow[-1] and ema_fast[-2] >= ema_slow[-2]
                         and rsi_val > params.rsi_os)

        if not is_cross_up and not is_cross_down:
            return None

        direction = "BUY" if is_cross_up else "SELL"

        # --- EMA200 Trend Filter ---
        if "ema200_filter" in filters:
            ema200 = _ema200(sl_data)
            if ema200 is not None:
                if direction == "BUY" and price < ema200:
                    return None
                if direction == "SELL" and price > ema200:
                    return None

        # --- BOS Guard ---
        if "bos_guard" in filters:
            bos = _detect_bos_simple(highs[:i + 1], lows[:i + 1])
            if direction == "BUY" and bos != "bullish":
                return None
            if direction == "SELL" and bos != "bearish":
                return None

        # --- FVG Guard ---
        if "fvg_guard" in filters:
            if not _has_fvg(highs[:i + 1], lows[:i + 1], direction):
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
            if bar_range > 0:
                body_pct = (body / bar_range) * 100
                if atr > 0 and bar_range / atr > 2.5 and body_pct < 30:
                    return None

        # --- VWAP Guard ---
        if "vwap_guard" in filters:
            vwap_proxy = float(ema_fast[-1])
            dist = abs(price - vwap_proxy)
            if atr > 0 and dist / atr > 1.5:
                return None

        # --- MACD Filter ---
        if "macd_filter" in filters and i >= params.macd_slow + params.macd_signal:
            macd_fast_ema = _ema(sl_data, params.macd_fast)
            macd_slow_ema = _ema(sl_data, params.macd_slow)
            macd_line = macd_fast_ema - macd_slow_ema
            macd_sig = _ema_from_array(macd_line[-params.macd_signal * 3:], params.macd_signal)
            histogram = macd_line[-1] - macd_sig[-1] if len(macd_sig) > 0 else 0
            if direction == "BUY" and histogram < 0:
                return None
            if direction == "SELL" and histogram > 0:
                return None

        # --- Bollinger Filter ---
        if "bollinger_filter" in filters and i >= params.bb_period:
            bb_slice = closes[i - params.bb_period + 1:i + 1]
            bb_mid = float(np.mean(bb_slice))
            bb_std = float(np.std(bb_slice))
            bb_upper = bb_mid + params.bb_std_dev * bb_std
            bb_lower = bb_mid - params.bb_std_dev * bb_std
            if bb_std > 0:
                pct_b = (price - bb_lower) / (bb_upper - bb_lower)
                # BUY near lower band (pct_b < 0.4), SELL near upper (pct_b > 0.6)
                if direction == "BUY" and pct_b > 0.7:
                    return None
                if direction == "SELL" and pct_b < 0.3:
                    return None

        # --- ADX Filter ---
        if "adx_filter" in filters and i >= params.adx_period * 2:
            adx_val = _adx_simple(highs[:i + 1], lows[:i + 1], closes[:i + 1], params.adx_period)
            if adx_val < params.adx_min_threshold:
                return None

        # --- Volume Filter ---
        if "volume_filter" in filters and hasattr(_filters, '__len__'):
            # Volume data not available in standard backtest args;
            # this filter is a no-op in backtests without volume data.
            # In live trading (AlgorithmicSignalEngine), volume comes from context.
            pass

        # --- Swing Structure Filter ---
        if "swing_structure" in filters and i >= 20:
            swing = _swing_structure_simple(highs[:i + 1], lows[:i + 1])
            if direction == "BUY" and swing != "bullish":
                return None
            if direction == "SELL" and swing != "bearish":
                return None

        # Build trade with tunable SL/TP multipliers
        sl_dist = params.sl_atr_mult * atr
        tp1_dist = sl_dist * params.tp1_rr
        tp2_dist = sl_dist * params.tp2_rr

        if direction == "BUY":
            return (TradeDirection.BUY, price, price - sl_dist, price + tp1_dist, price + tp2_dist, "ema_cross", 0.75)
        else:
            return (TradeDirection.SELL, price, price + sl_dist, price - tp1_dist, price - tp2_dist, "ema_cross", 0.75)

    return signal_fn


async def _run_engine_in_thread(**kwargs):
    """Run BacktestEngine.run() in a thread pool to avoid blocking the event loop.
    Creates a fresh event loop + lightweight engine (no DB) in the thread."""
    def _run():
        eng = BacktestEngine()  # no session_factory — runner handles DB
        return asyncio.run(eng.run(**kwargs))
    return await asyncio.to_thread(_run)


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
                      session_factory, timeframe, tools or [], name=name)
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
) -> None:
    """Background coroutine that runs one backtest."""
    tools = tools or []
    _log(run_id, f"Starting backtest: {symbol}, {days}d, ${balance:.0f}, {max_generations} gens, TF={timeframe}")
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

        async with session_factory() as session:
            repo = BacktestRepository(session)
            await repo.update_progress(run_id, generation=0, phase="data_loaded",
                                       message=f"{len(closes)} bars loaded")
            run = await repo.get_by_run_id(run_id)
            if run:
                run.bars_loaded = len(closes)
            await session.commit()

        from alphaloop.backtester.optimizer import (
            optimize, split_data, MIN_SHARPE_IMPROVEMENT, OVERFIT_GAP_THRESHOLD,
        )

        engine = BacktestEngine(session_factory=session_factory)
        base_params = BacktestParams()
        best_params = base_params
        best_sharpe = -999.0
        best_result = None
        no_improve_count = 0

        # Split data for train/validation
        train_data, val_data = split_data(opens, highs, lows, closes, timestamps)
        _log(run_id, f"Split: {len(train_data['closes'])} train bars, {len(val_data['closes'])} val bars", "DATA")

        # ── Check for checkpoint (resume support) ────────────────────────
        d_hash = _data_hash(closes)
        cp_params, cp_gen, cp_sharpe = _load_checkpoint(run_id, d_hash)
        resume_from_gen = 0
        if cp_params is not None and cp_gen > 0:
            best_params = cp_params
            best_sharpe = cp_sharpe
            resume_from_gen = cp_gen
            _log(run_id, f"Resuming from checkpoint: gen={cp_gen}, sharpe={cp_sharpe:.3f}", "CKPT")
            # Run on full data with restored params to get best_result
            sig_fn_cp = make_signal_fn(best_params, tools)
            best_result = await _run_engine_in_thread(
                symbol=symbol,
                opens=opens, highs=highs, lows=lows, closes=closes,
                timestamps=timestamps, balance=balance,
                risk_pct=best_params.risk_pct, filters=tools,
                signal_fn=sig_fn_cp, run_id=f"{run_id}_resume",
                stop_check=lambda: _stop_flags.get(run_id, False),
            )
            _log(run_id, f"Restored result: {len(best_result.closed_trades)} trades, "
                         f"Sharpe={best_result.sharpe or '—'}", "STAT")

        if resume_from_gen == 0:
            # ── Gen 1: Baseline ──────────────────────────────────────────
            _log(run_id, f"=== Generation 1/{max_generations}: Baseline ===", "GEN")
            t0 = time.time()
            sig_fn = make_signal_fn(base_params, tools)
            result = await _run_engine_in_thread(
                symbol=symbol,
                opens=opens, highs=highs, lows=lows, closes=closes,
                timestamps=timestamps, balance=balance,
                risk_pct=base_params.risk_pct, filters=tools,
                signal_fn=sig_fn, run_id=f"{run_id}_g1",
                stop_check=lambda: _stop_flags.get(run_id, False),
            )
            elapsed = time.time() - t0
            s = result.summary()
            baseline_sharpe = result.sharpe or -999.0
            best_sharpe = baseline_sharpe
            best_result = result
            _log(run_id, f"Baseline: {s['total_trades']} trades, WR={s['win_rate']:.1%}, "
                         f"Sharpe={s['sharpe'] or '—'}, PnL=${s['total_pnl']:.2f}, "
                         f"DD={s['max_dd_pct']:.1f}% ({elapsed:.1f}s)", "STAT")
            _log(run_id, f"Params: EMA={base_params.ema_fast}/{base_params.ema_slow}, "
                         f"SL={base_params.sl_atr_mult}, TP1={base_params.tp1_rr}, TP2={base_params.tp2_rr}, "
                         f"RSI={base_params.rsi_os}-{base_params.rsi_ob}", "STAT")

            # Save checkpoint after baseline
            cp_path = _save_checkpoint(run_id, 1, best_params, best_sharpe, d_hash)
            async with session_factory() as session:
                repo = BacktestRepository(session)
                await repo.update_progress(
                    run_id, generation=1, phase="baseline",
                    message=f"Baseline: WR={s['win_rate']:.1%} Sharpe={s['sharpe'] or '—'}",
                    best_sharpe=best_sharpe if best_sharpe > -999 else None,
                    best_wr=result.win_rate, best_pnl=result.total_pnl,
                    best_dd=result.max_drawdown_pct,
                    best_trades=len(result.closed_trades),
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
                        best_trades=len(best_result.closed_trades) if best_result else None,
                    )
                    await repo.update_state(run_id, "paused",
                                            message=f"Stopped at generation {gen - 1}")
                    await session.commit()
                return

            _log(run_id, f"=== Generation {gen}/{max_generations}: Optimizing ===", "GEN")

            # Run Optuna + backtests entirely in a thread to avoid blocking the event loop.
            # The engine's signal_fn is async but only does numpy — safe to run via asyncio.run().
            def run_on_train(params: BacktestParams) -> float:
                """Sync: run backtest on train split inside a fresh event loop."""
                sig_fn = make_signal_fn(params, tools)
                try:
                    r = asyncio.run(engine.run(
                        symbol=symbol,
                        opens=train_data["opens"], highs=train_data["highs"],
                        lows=train_data["lows"], closes=train_data["closes"],
                        timestamps=train_data["timestamps"], balance=balance,
                        risk_pct=params.risk_pct, filters=tools, signal_fn=sig_fn,
                        stop_check=lambda: _stop_flags.get(run_id, False),
                    ))
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
                        best_trades=len(best_result.closed_trades) if best_result else None,
                    )
                    await repo.update_state(run_id, "paused",
                                            message=f"Stopped during gen {gen} — checkpoint at gen {gen - 1}")
                    await session.commit()
                return

            if opt_params is None or train_sharpe <= best_sharpe + MIN_SHARPE_IMPROVEMENT:
                no_improve_count += 1
                _log(run_id, f"Gen {gen}: No improvement (train Sharpe={train_sharpe:.3f} vs best={best_sharpe:.3f}). "
                             f"No-improve streak: {no_improve_count}", "STAT")
                if no_improve_count >= 2:
                    _log(run_id, "Early stop — no improvement for 2 consecutive generations", "WARN")
                    break
            else:
                # Validate on holdout
                _log(run_id, f"Gen {gen}: Train Sharpe={train_sharpe:.3f} — validating on holdout...", "STAT")
                sig_fn_val = make_signal_fn(opt_params, tools)
                val_result = await _run_engine_in_thread(
                    symbol=symbol,
                    opens=val_data["opens"], highs=val_data["highs"],
                    lows=val_data["lows"], closes=val_data["closes"],
                    timestamps=val_data["timestamps"], balance=balance,
                    risk_pct=opt_params.risk_pct, filters=tools, signal_fn=sig_fn_val,
                )
                val_sharpe = val_result.sharpe or -999.0
                gap = train_sharpe - val_sharpe

                _log(run_id, f"  Validation: Sharpe={val_sharpe:.3f}, gap={gap:.3f}", "STAT")

                if gap > OVERFIT_GAP_THRESHOLD:
                    _log(run_id, f"  OVERFIT DETECTED (gap {gap:.3f} > {OVERFIT_GAP_THRESHOLD}) — skipping", "WARN")
                    no_improve_count += 1
                else:
                    # Run on full data to confirm
                    sig_fn_full = make_signal_fn(opt_params, tools)
                    full_result = await _run_engine_in_thread(
                        symbol=symbol,
                        opens=opens, highs=highs, lows=lows, closes=closes,
                        timestamps=timestamps, balance=balance,
                        risk_pct=opt_params.risk_pct, filters=tools,
                        signal_fn=sig_fn_full, run_id=f"{run_id}_g{gen}",
                        stop_check=lambda: _stop_flags.get(run_id, False),
                    )
                    full_sharpe = full_result.sharpe or -999.0
                    fs = full_result.summary()

                    if full_sharpe > best_sharpe + MIN_SHARPE_IMPROVEMENT:
                        best_sharpe = full_sharpe
                        best_params = opt_params
                        best_result = full_result
                        no_improve_count = 0
                        _log(run_id, f"  ACCEPTED: {fs['total_trades']} trades, WR={fs['win_rate']:.1%}, "
                                     f"Sharpe={fs['sharpe'] or '—'}, PnL=${fs['total_pnl']:.2f}", "STAT")
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
                    best_trades=len(best_result.closed_trades) if best_result else None,
                )
                run_obj = await repo.get_by_run_id(run_id)
                if run_obj:
                    run_obj.checkpoint_path = cp_path
                await session.commit()

        # Completed
        _log(run_id, "=" * 50, "GEN")
        _log(run_id, "Backtest completed!", "GEN")
        if best_result:
            s = best_result.summary()
            _log(run_id, f"Best: {s['total_trades']} trades, WR={s['win_rate']:.1%}, "
                         f"Sharpe={s['sharpe'] or '—'}, PnL=${s['total_pnl']:.2f}", "STAT")
            _log(run_id, f"Best params: EMA={best_params.ema_fast}/{best_params.ema_slow}, "
                         f"SL={best_params.sl_atr_mult}, TP1={best_params.tp1_rr}, "
                         f"TP2={best_params.tp2_rr}, RSI={best_params.rsi_os}-{best_params.rsi_ob}", "STAT")

            # Auto-create strategy version file from best result
            try:
                from alphaloop.backtester.asset_trainer import create_strategy_version
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
                    tools=tools,
                    status="candidate",
                    source="backtest_runner",
                    name=name or run_id,
                )
                _log(run_id, f"Strategy version created: {symbol} v{version_data['_version']}")
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

    # Estimate bars needed
    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1wk": 10080, "1mo": 43200}
    minutes_per_bar = tf_minutes.get(timeframe, 60)
    bars_needed = int((days * 24 * 60) / minutes_per_bar)
    bars_needed = max(bars_needed, 200)

    def _mt5_fetch():
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
            if broker.password:
                kwargs["password"] = broker.password
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
            return None
        # Try the symbol as-is first, then with 'm' suffix (broker convention)
        for sym in [symbol, symbol + "m", symbol.rstrip("m")]:
            rates = mt5.copy_rates_from_pos(sym, mt5_tf, 0, bars_needed)
            if rates is not None and len(rates) > 0:
                return rates
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
