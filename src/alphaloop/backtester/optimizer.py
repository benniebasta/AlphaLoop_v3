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

logger = logging.getLogger(__name__)

# Suppress optuna's verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)

# ── Constants ────────────────────────────────────────────────────────────────
MIN_SHARPE_IMPROVEMENT = 0.05   # must improve by at least this
OVERFIT_GAP_THRESHOLD = 0.30    # train-val gap above this = overfitting
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


def suggest_params(trial: optuna.Trial, base: BacktestParams) -> BacktestParams:
    """Suggest new parameters bounded around the base values."""
    mc = base.max_param_change_pct  # ±15% default

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
            _log(f"  Trial {trial.number}: new best Sharpe={sharpe:.3f} "
                 f"(SL={params.sl_atr_mult}, TP1={params.tp1_rr}, TP2={params.tp2_rr}, "
                 f"EMA={params.ema_fast}/{params.ema_slow}, RSI={params.rsi_os}-{params.rsi_ob})")

        return sharpe

    _log(f"Running {n_trials} Optuna trials (TPE sampler)...")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    _log(f"Optimization done: best Sharpe={best_sharpe:.3f}")

    return best_params, best_sharpe, was_stopped
