"""
Optuna-based parameter optimizer for backtesting.

Uses TPE (Tree-structured Parzen Estimator) sampler to search for
optimal strategy parameters. Includes train/val split and overfitting detection.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np
import optuna

from alphaloop.backtester.params import BacktestParams
from alphaloop.config.assets import AssetConfig
from alphaloop.trading.strategy_loader import (
    build_algorithmic_params,
    build_strategy_resolution_input,
    normalize_strategy_tools,
    resolve_strategy_setup_family,
    resolve_strategy_signal_mode,
    resolve_strategy_source,
    serialize_strategy_spec,
)

logger = logging.getLogger(__name__)

# Suppress optuna's verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Constants
MIN_SHARPE_IMPROVEMENT = 0.05   # must improve by at least this
OVERFIT_GAP_THRESHOLD = 0.30    # train-val gap above this = overfitting
MIN_TRADES = 20                 # minimum closed trades for a result to be statistically valid
N_TRIALS_PER_GEN = 30           # Optuna trials per generation
N_STARTUP_TRIALS = 5            # random trials before TPE kicks in
TRAIN_RATIO = 0.80              # 80% train, 20% validation


def split_data(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timestamps: list | None = None,
    ratio: float = TRAIN_RATIO,
) -> tuple[dict, dict]:
    """Split OHLCV arrays into train and validation sets by time."""
    n = len(closes)
    split_idx = int(n * ratio)

    train = {
        "opens": opens[:split_idx],
        "highs": highs[:split_idx],
        "lows": lows[:split_idx],
        "closes": closes[:split_idx],
        "timestamps": timestamps[:split_idx] if timestamps else None,
    }
    val = {
        "opens": opens[split_idx:],
        "highs": highs[split_idx:],
        "lows": lows[split_idx:],
        "closes": closes[split_idx:],
        "timestamps": timestamps[split_idx:] if timestamps else None,
    }
    return train, val


_ALL_SOURCES = [
    "ema_crossover",
    "macd_crossover",
    "rsi_reversal",
    "bollinger_breakout",
    "adx_trend",
    "bos_confirm",
]


def _strategy_metadata(base: BacktestParams) -> tuple[str, str, str]:
    strategy_like = build_strategy_resolution_input(base, tools=base.tools)
    resolved_params = build_algorithmic_params(strategy_like)
    strategy_like["params"] = resolved_params
    return (
        resolve_strategy_signal_mode(strategy_like),
        resolve_strategy_setup_family(strategy_like),
        resolve_strategy_source(strategy_like),
    )


def _serialized_strategy_spec(base: BacktestParams) -> dict[str, Any]:
    strategy_like = build_strategy_resolution_input(base, tools=base.tools)
    strategy_like["params"] = build_algorithmic_params(strategy_like)
    return serialize_strategy_spec(strategy_like)


def suggest_params(trial: optuna.Trial, base: BacktestParams) -> BacktestParams:
    """Suggest new parameters bounded around the base values."""
    mc = base.max_param_change_pct  # +/-15% default
    resolved_signal_mode, resolved_setup_family, resolved_source = _strategy_metadata(base)

    sl = trial.suggest_float(
        "sl_atr_mult",
        max(0.8, base.sl_atr_mult * (1 - mc)),
        base.sl_atr_mult * (1 + mc * 2),
    )
    tp1 = trial.suggest_float(
        "tp1_rr",
        max(1.3, base.tp1_rr * (1 - mc)),
        base.tp1_rr * (1 + mc),
    )
    tp2 = trial.suggest_float(
        "tp2_rr",
        max(tp1 + 0.5, base.tp2_rr * (1 - mc)),
        base.tp2_rr * (1 + mc),
    )
    rsi_ob = trial.suggest_int("rsi_ob", 65, 80)
    rsi_os = trial.suggest_int("rsi_os", 20, 35)

    ema_fast = trial.suggest_int("ema_fast", 10, 30)
    ema_slow = trial.suggest_int("ema_slow", 40, 80)

    # Prune invalid combos
    if tp1 < 1.3 or sl < 0.8 or ema_fast >= ema_slow:
        raise optuna.TrialPruned()

    # Source-specific params
    resolved_base_params = build_algorithmic_params(
        build_strategy_resolution_input(base, tools=base.tools)
    )
    active_sources = {r.get("source") for r in (resolved_base_params.get("signal_rules") or [])}

    macd_fast = base.macd_fast
    macd_slow = base.macd_slow
    macd_signal = base.macd_signal
    bb_period = base.bb_period
    bb_std_dev = base.bb_std_dev
    adx_period = base.adx_period
    adx_min_threshold = base.adx_min_threshold

    if "macd_crossover" in active_sources:
        macd_fast = trial.suggest_int("macd_fast", 8, 16)
        macd_slow = trial.suggest_int("macd_slow", 20, 32)
        macd_signal = trial.suggest_int("macd_signal", 7, 11)
    if "bollinger_breakout" in active_sources:
        bb_period = trial.suggest_int("bb_period", 15, 25)
        bb_std_dev = trial.suggest_float("bb_std_dev", 1.5, 2.5)
    if "adx_trend" in active_sources:
        adx_period = trial.suggest_int("adx_period", 10, 20)
        adx_min_threshold = trial.suggest_float("adx_min_threshold", 15.0, 35.0)

    # signal_auto: Optuna picks which sources to enable
    signal_rules = list(resolved_base_params.get("signal_rules") or [])
    signal_logic = resolved_base_params.get("signal_logic") or "AND"
    if base.signal_auto:
        flags = {src: trial.suggest_categorical(f"use_{src}", [True, False]) for src in _ALL_SOURCES}
        active = [src for src, on in flags.items() if on]
        if not active:
            raise optuna.TrialPruned()
        signal_rules = [{"source": s} for s in active]
        signal_logic = trial.suggest_categorical("signal_logic", ["AND", "OR", "MAJORITY"])
        # Re-suggest source params based on auto-selected sources
        if "macd_crossover" in flags and flags["macd_crossover"]:
            macd_fast = trial.suggest_int("macd_fast_auto", 8, 16)
            macd_slow = trial.suggest_int("macd_slow_auto", 20, 32)
            macd_signal = trial.suggest_int("macd_signal_auto", 7, 11)
        if "bollinger_breakout" in flags and flags["bollinger_breakout"]:
            bb_period = trial.suggest_int("bb_period_auto", 15, 25)
            bb_std_dev = trial.suggest_float("bb_std_dev_auto", 1.5, 2.5)
        if "adx_trend" in flags and flags["adx_trend"]:
            adx_period = trial.suggest_int("adx_period_auto", 10, 20)
            adx_min_threshold = trial.suggest_float("adx_min_threshold_auto", 15.0, 35.0)

    return BacktestParams(
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        sl_atr_mult=round(sl, 3),
        tp1_rr=round(tp1, 3),
        tp2_rr=round(tp2, 3),
        rsi_ob=rsi_ob,
        rsi_os=rsi_os,
        risk_pct=base.risk_pct,
        rsi_period=base.rsi_period,
        max_param_change_pct=mc,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        bb_period=bb_period,
        bb_std_dev=round(bb_std_dev, 2),
        adx_period=adx_period,
        adx_min_threshold=round(adx_min_threshold, 1),
        signal_rules=signal_rules,
        signal_logic=signal_logic,
        signal_auto=base.signal_auto,
        signal_mode=resolved_signal_mode,
        setup_family=resolved_setup_family,
        strategy_spec=_serialized_strategy_spec(base),
        tools=normalize_strategy_tools(base.tools),
        source=resolved_source,
    )


def optimize(
    base_params: BacktestParams,
    run_backtest_fn: Callable[[BacktestParams], float],
    n_trials: int = N_TRIALS_PER_GEN,
    stop_check: Callable[[], bool] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[BacktestParams | None, float, bool]:
    """
    Run Optuna optimization to find better parameters.

    Args:
        base_params: Starting parameters (center of search space).
        run_backtest_fn: Callable(params) -> sharpe_ratio (or -999 on error).
        n_trials: Number of trials to run.
        stop_check: Optional callable returning True to abort.
        log_fn: Optional logging callback.

    Returns:
        (best_params, best_sharpe, was_stopped)
    """
    _log = log_fn or (lambda msg: None)
    best_sharpe = -999.0
    best_params: BacktestParams | None = None
    was_stopped = False

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(
            n_startup_trials=N_STARTUP_TRIALS,
            seed=42,
        ),
    )

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_sharpe, best_params, was_stopped

        if stop_check and stop_check():
            was_stopped = True
            study.stop()
            return best_sharpe

        try:
            params = suggest_params(trial, base_params)
        except optuna.TrialPruned:
            raise

        sharpe = run_backtest_fn(params)

        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = params
            _log(
                f"  Trial {trial.number}: new best Sharpe={sharpe:.3f} "
                f"(SL={params.sl_atr_mult}, TP1={params.tp1_rr}, TP2={params.tp2_rr}, "
                f"EMA={params.ema_fast}/{params.ema_slow}, RSI={params.rsi_os}-{params.rsi_ob})"
            )

        return sharpe

    _log(f"Running {n_trials} Optuna trials (TPE sampler)...")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    _log(f"Optimization done: best Sharpe={best_sharpe:.3f}")

    return best_params, best_sharpe, was_stopped


# ---------------------------------------------------------------------------
# Construction-aware optimization (Part 2: uses TradeConstructor via vbt)
# ---------------------------------------------------------------------------

def suggest_construction_params(
    trial: optuna.Trial,
    base: BacktestParams,
    asset: AssetConfig | None = None,
) -> dict:
    """Suggest construction-compatible parameters for Optuna.

    Unlike :func:`suggest_params`, this does NOT suggest ``sl_atr_mult``
    because SL is structure-derived in the constraint-first architecture.
    Instead it suggests bounds that constrain the structure search.
    """
    mc = base.max_param_change_pct
    resolved_signal_mode, resolved_setup_family, resolved_source = _strategy_metadata(base)

    # Construction params (no sl_atr_mult)
    tp1_rr = trial.suggest_float("tp1_rr", 1.2, 3.0)
    tp2_rr = trial.suggest_float("tp2_rr", max(tp1_rr + 0.3, 2.0), 5.0)

    sl_min = trial.suggest_float("sl_min_points", 50, 300)
    sl_max = trial.suggest_float("sl_max_points", max(sl_min + 100, 200), 800)
    sl_buffer = trial.suggest_float("sl_buffer_atr", 0.05, 0.30)

    confidence_threshold = trial.suggest_float("confidence_threshold", 0.50, 0.90)
    entry_zone_mult = trial.suggest_float("entry_zone_atr_mult", 0.10, 0.50)

    # Direction params (same as legacy)
    ema_fast = trial.suggest_int("ema_fast", 10, 30)
    ema_slow = trial.suggest_int("ema_slow", max(ema_fast + 15, 40), 80)
    rsi_ob = trial.suggest_int("rsi_ob", 65, 80)
    rsi_os = trial.suggest_int("rsi_os", 20, 35)

    if ema_fast >= ema_slow:
        raise optuna.TrialPruned()

    # Signal rules from base (not mutated - source selection is structural)
    resolved_base_params = build_algorithmic_params(
        build_strategy_resolution_input(base, tools=base.tools)
    )
    signal_rules = list(resolved_base_params.get("signal_rules") or [])
    signal_logic = resolved_base_params.get("signal_logic") or "AND"

    return {
        "tp1_rr": round(tp1_rr, 3),
        "tp2_rr": round(tp2_rr, 3),
        "sl_min_points": round(sl_min, 1),
        "sl_max_points": round(sl_max, 1),
        "sl_buffer_atr": round(sl_buffer, 3),
        "confidence_threshold": round(confidence_threshold, 3),
        "entry_zone_atr_mult": round(entry_zone_mult, 3),
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi_ob": rsi_ob,
        "rsi_os": rsi_os,
        "signal_rules": signal_rules,
        "signal_logic": signal_logic,
        "signal_mode": resolved_signal_mode,
        "setup_family": resolved_setup_family,
        "strategy_spec": _serialized_strategy_spec(base),
        "tools": normalize_strategy_tools(base.tools),
        "source": resolved_source,
    }


def optimize_construction(
    ohlcv_df,
    asset_config: AssetConfig,
    base_params: BacktestParams,
    *,
    n_trials: int = N_TRIALS_PER_GEN,
    stop_check: Callable[[], bool] | None = None,
    log_fn: Callable[[str], None] | None = None,
    balance: float = 10_000.0,
    risk_pct: float = 0.01,
) -> tuple[dict | None, float, bool]:
    """Run Optuna optimization using the vectorbt + TradeConstructor path.

    Returns
    -------
    (best_params, best_score, was_stopped)
    """
    from alphaloop.backtester.vbt_engine import run_vectorbt_backtest

    _log = log_fn or (lambda msg: None)
    best_score = -999.0
    best_params_dict: dict | None = None
    was_stopped = False

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(
            n_startup_trials=N_STARTUP_TRIALS,
            seed=42,
        ),
    )

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_score, best_params_dict, was_stopped

        if stop_check and stop_check():
            was_stopped = True
            study.stop()
            return best_score

        try:
            params = suggest_construction_params(trial, base_params, asset_config)
        except optuna.TrialPruned:
            raise

        result = run_vectorbt_backtest(
            ohlcv_df, params, asset_config,
            balance=balance, risk_pct=risk_pct,
        )

        if result.error:
            _log(f"  Trial {trial.number}: error - {result.error}")
            return -999.0

        if result.trade_count < MIN_TRADES:
            raise optuna.TrialPruned()

        # Composite score: Sharpe - drawdown penalty
        sharpe = result.sharpe or 0.0
        dd_penalty = max(0, abs(result.max_drawdown_pct) - 5) * 0.1
        score = sharpe - dd_penalty

        if score > best_score:
            best_score = score
            best_params_dict = params
            _log(
                f"  Trial {trial.number}: new best score={score:.3f} "
                f"(Sharpe={sharpe:.3f} DD={result.max_drawdown_pct:.1f}% "
                f"trades={result.trade_count} exec_rate={result.execution_rate:.1%})"
            )

        return score

    _log(f"Running {n_trials} construction-aware Optuna trials...")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    _log(f"Optimization done: best score={best_score:.3f}")

    return best_params_dict, best_score, was_stopped
