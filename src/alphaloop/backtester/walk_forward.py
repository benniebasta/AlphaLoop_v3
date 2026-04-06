"""
backtester/walk_forward.py — Walk-forward validation framework.

Prevents overfitting by splitting data into in-sample (IS) and out-of-sample
(OOS) windows, optimizing parameters on IS only, and validating on OOS with
fixed parameters.

Promotion gate:
    OOS Sharpe ≥ ``oos_sharpe_ratio`` × IS Sharpe  (default 0.70)

This catches parameter sets that look great on training data but degrade
significantly on unseen data — the most common form of backtest overfitting
in discretionary-style rule systems.

Usage::

    from alphaloop.backtester.walk_forward import WalkForwardEngine

    engine = WalkForwardEngine()
    result = engine.run(ohlcv_df, base_params, asset_config)
    if result.passes_gate:
        deploy(result.best_params)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

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

# ── Defaults ─────────────────────────────────────────────────────────────────
IS_RATIO = 0.70                  # 70% in-sample, 30% out-of-sample
OOS_SHARPE_RATIO = 0.70          # OOS Sharpe must be ≥ 70% of IS Sharpe
MIN_OOS_TRADES = 10              # gate fails if OOS produces fewer trades
N_TRIALS_DEFAULT = 40            # Optuna trials for IS optimization


def _with_strategy_metadata(params: dict[str, Any], base_params: BacktestParams) -> dict[str, Any]:
    """Preserve non-tuned strategy metadata across walk-forward evaluation."""
    merged = dict(params)
    strategy_like = build_strategy_resolution_input(base_params, tools=base_params.tools)
    resolved_params = build_algorithmic_params(strategy_like)
    merged.setdefault("signal_mode", resolve_strategy_signal_mode(strategy_like))
    merged.setdefault("setup_family", resolve_strategy_setup_family(strategy_like))
    merged.setdefault("strategy_spec", serialize_strategy_spec(strategy_like))
    merged.setdefault("tools", normalize_strategy_tools(base_params.tools))
    merged.setdefault("source", resolve_strategy_source(strategy_like))
    merged.setdefault("signal_rules", list(resolved_params.get("signal_rules") or []))
    merged.setdefault("signal_logic", resolved_params.get("signal_logic") or "AND")
    return merged


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class WalkForwardResult:
    """Full output of a walk-forward evaluation run."""

    # Optimized params (from IS)
    best_params: dict[str, Any] = field(default_factory=dict)

    # In-sample metrics
    is_sharpe: float | None = None
    is_trade_count: int = 0
    is_win_rate: float = 0.0
    is_max_drawdown_pct: float = 0.0

    # Out-of-sample metrics (fixed params, no optimisation)
    oos_sharpe: float | None = None
    oos_trade_count: int = 0
    oos_win_rate: float = 0.0
    oos_max_drawdown_pct: float = 0.0

    # Gate
    oos_sharpe_ratio_required: float = OOS_SHARPE_RATIO
    passes_gate: bool = False
    gate_reason: str = ""

    # Data split info
    is_bars: int = 0
    oos_bars: int = 0

    # Error
    error: str | None = None

    @property
    def degradation_ratio(self) -> float | None:
        """OOS Sharpe / IS Sharpe — 1.0 means perfect generalisation."""
        if self.is_sharpe and self.oos_sharpe is not None and self.is_sharpe > 0:
            return round(self.oos_sharpe / self.is_sharpe, 3)
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "passes_gate": self.passes_gate,
            "gate_reason": self.gate_reason,
            "is_sharpe": self.is_sharpe,
            "oos_sharpe": self.oos_sharpe,
            "degradation_ratio": self.degradation_ratio,
            "is_trades": self.is_trade_count,
            "oos_trades": self.oos_trade_count,
            "is_win_rate": round(self.is_win_rate, 3),
            "oos_win_rate": round(self.oos_win_rate, 3),
            "is_max_dd_pct": self.is_max_drawdown_pct,
            "oos_max_dd_pct": self.oos_max_drawdown_pct,
            "is_bars": self.is_bars,
            "oos_bars": self.oos_bars,
        }


# ── Engine ────────────────────────────────────────────────────────────────────

class WalkForwardEngine:
    """
    Walk-forward validation engine.

    Splits OHLCV data into IS (optimisation) and OOS (validation) windows,
    runs Optuna IS optimisation, then evaluates the best params on OOS with
    no further parameter changes.

    The promotion gate rejects parameter sets where OOS Sharpe drops below
    ``oos_sharpe_ratio`` × IS Sharpe, flagging overfitting before deployment.
    """

    def __init__(
        self,
        *,
        is_ratio: float = IS_RATIO,
        oos_sharpe_ratio: float = OOS_SHARPE_RATIO,
        min_oos_trades: int = MIN_OOS_TRADES,
        n_trials: int = N_TRIALS_DEFAULT,
    ) -> None:
        self.is_ratio = is_ratio
        self.oos_sharpe_ratio = oos_sharpe_ratio
        self.min_oos_trades = min_oos_trades
        self.n_trials = n_trials

    def run(
        self,
        ohlcv_df: pd.DataFrame,
        base_params: BacktestParams,
        asset_config: AssetConfig | None = None,
        *,
        balance: float = 10_000.0,
        risk_pct: float = 0.01,
        stop_check: Callable[[], bool] | None = None,
        log_fn: Callable[[str], None] | None = None,
        symbol: str = "",
    ) -> WalkForwardResult:
        """
        Run a walk-forward validation cycle.

        Parameters
        ----------
        ohlcv_df:
            Full OHLCV DataFrame (must have open/high/low/close columns).
        base_params:
            Starting parameters for IS optimisation.
        asset_config:
            Asset-specific config passed to TradeConstructor.
        balance:
            Starting account balance for both IS and OOS passes.
        risk_pct:
            Risk per trade fraction.
        stop_check:
            Optional callable returning True to abort early.
        log_fn:
            Optional logging callback for per-trial progress.
        symbol:
            Trading symbol (for log messages only).

        Returns
        -------
        WalkForwardResult
        """
        from alphaloop.backtester.optimizer import optimize_construction
        from alphaloop.backtester.vbt_engine import run_vectorbt_backtest

        _log = log_fn or (lambda msg: logger.debug("[walk-forward] %s", msg))
        result = WalkForwardResult(oos_sharpe_ratio_required=self.oos_sharpe_ratio)

        # ── 1. Validate data ─────────────────────────────────────────────────
        if ohlcv_df is None or len(ohlcv_df) < 100:
            result.error = f"Insufficient data: {len(ohlcv_df) if ohlcv_df is not None else 0} bars (need ≥100)"
            result.gate_reason = result.error
            return result

        # ── 2. Split ─────────────────────────────────────────────────────────
        n = len(ohlcv_df)
        split_idx = int(n * self.is_ratio)
        if split_idx < 50 or (n - split_idx) < 30:
            result.error = f"Split produces too-small windows (IS={split_idx}, OOS={n-split_idx})"
            result.gate_reason = result.error
            return result

        is_df = ohlcv_df.iloc[:split_idx].copy()
        oos_df = ohlcv_df.iloc[split_idx:].copy()

        result.is_bars = len(is_df)
        result.oos_bars = len(oos_df)

        _log(
            f"{symbol} walk-forward split: {result.is_bars} IS bars / "
            f"{result.oos_bars} OOS bars"
        )

        # ── 3. IS optimisation ───────────────────────────────────────────────
        _log(f"{symbol} running IS optimisation ({self.n_trials} trials)...")
        try:
            best_params_dict, is_score, was_stopped = optimize_construction(
                is_df,
                asset_config,
                base_params,
                n_trials=self.n_trials,
                stop_check=stop_check,
                log_fn=_log,
                balance=balance,
                risk_pct=risk_pct,
            )
        except Exception as exc:
            result.error = f"IS optimisation failed: {exc}"
            result.gate_reason = result.error
            logger.error("[walk-forward] IS optimisation error: %s", exc)
            return result

        if best_params_dict is None:
            result.error = "IS optimisation produced no valid params"
            result.gate_reason = result.error
            return result

        best_params_dict = _with_strategy_metadata(best_params_dict, base_params)

        if was_stopped:
            _log(f"{symbol} IS optimisation stopped early by stop_check")

        # ── 4. IS evaluation (fixed best params, no further search) ──────────
        try:
            is_result = run_vectorbt_backtest(
                is_df, best_params_dict, asset_config,
                symbol=symbol, balance=balance, risk_pct=risk_pct,
            )
        except Exception as exc:
            result.error = f"IS evaluation failed: {exc}"
            result.gate_reason = result.error
            return result

        result.best_params = best_params_dict
        result.is_sharpe = is_result.sharpe
        result.is_trade_count = is_result.trade_count
        result.is_win_rate = is_result.win_rate
        result.is_max_drawdown_pct = is_result.max_drawdown_pct

        _log(
            f"{symbol} IS result: Sharpe={is_result.sharpe} "
            f"trades={is_result.trade_count} WR={is_result.win_rate:.1%} "
            f"DD={is_result.max_drawdown_pct:.1f}%"
        )

        # ── 5. OOS evaluation (fixed params, no optimisation) ────────────────
        _log(f"{symbol} running OOS evaluation with fixed IS params...")
        try:
            oos_result = run_vectorbt_backtest(
                oos_df, best_params_dict, asset_config,
                symbol=symbol, balance=balance, risk_pct=risk_pct,
            )
        except Exception as exc:
            result.error = f"OOS evaluation failed: {exc}"
            result.gate_reason = result.error
            return result

        result.oos_sharpe = oos_result.sharpe
        result.oos_trade_count = oos_result.trade_count
        result.oos_win_rate = oos_result.win_rate
        result.oos_max_drawdown_pct = oos_result.max_drawdown_pct

        _log(
            f"{symbol} OOS result: Sharpe={oos_result.sharpe} "
            f"trades={oos_result.trade_count} WR={oos_result.win_rate:.1%} "
            f"DD={oos_result.max_drawdown_pct:.1f}%"
        )

        # ── 6. Promotion gate ─────────────────────────────────────────────────
        result.passes_gate, result.gate_reason = self._evaluate_gate(result)

        if result.passes_gate:
            _log(f"{symbol} PASSED walk-forward gate: {result.gate_reason}")
        else:
            _log(f"{symbol} FAILED walk-forward gate: {result.gate_reason}")

        return result

    def _evaluate_gate(self, result: WalkForwardResult) -> tuple[bool, str]:
        """Apply promotion gate rules. Returns (passes, reason)."""

        # Minimum OOS trades
        if result.oos_trade_count < self.min_oos_trades:
            return (
                False,
                f"OOS produced only {result.oos_trade_count} trades "
                f"(need ≥{self.min_oos_trades})",
            )

        # IS Sharpe must be computable
        if result.is_sharpe is None:
            return False, "IS Sharpe could not be computed (too few trades)"

        # OOS Sharpe must be computable
        if result.oos_sharpe is None:
            return (
                False,
                f"OOS Sharpe could not be computed with {result.oos_trade_count} trades",
            )

        # OOS must be positive (negative Sharpe = net loser on unseen data)
        if result.oos_sharpe <= 0:
            return (
                False,
                f"OOS Sharpe={result.oos_sharpe:.3f} is negative (strategy loses on unseen data)",
            )

        # IS Sharpe must be positive for the ratio test to be meaningful
        if result.is_sharpe <= 0:
            return (
                False,
                f"IS Sharpe={result.is_sharpe:.3f} is non-positive; optimization found no edge",
            )

        # Core ratio test
        ratio = result.oos_sharpe / result.is_sharpe
        required = self.oos_sharpe_ratio
        if ratio < required:
            return (
                False,
                f"OOS/IS Sharpe ratio={ratio:.2f} < {required:.2f} required "
                f"(IS={result.is_sharpe:.3f}, OOS={result.oos_sharpe:.3f}) — overfitting detected",
            )

        return (
            True,
            f"OOS/IS ratio={ratio:.2f} ≥ {required:.2f} "
            f"(IS={result.is_sharpe:.3f}, OOS={result.oos_sharpe:.3f})",
        )


# ── Convenience wrapper ───────────────────────────────────────────────────────

def run_walk_forward(
    ohlcv_df: pd.DataFrame,
    base_params: BacktestParams,
    asset_config: AssetConfig | None = None,
    *,
    is_ratio: float = IS_RATIO,
    oos_sharpe_ratio: float = OOS_SHARPE_RATIO,
    n_trials: int = N_TRIALS_DEFAULT,
    balance: float = 10_000.0,
    risk_pct: float = 0.01,
    symbol: str = "",
    log_fn: Callable[[str], None] | None = None,
) -> WalkForwardResult:
    """
    Convenience wrapper: create a WalkForwardEngine and run one cycle.

    Returns a :class:`WalkForwardResult`.  Check ``result.passes_gate``
    before promoting ``result.best_params`` to production.
    """
    engine = WalkForwardEngine(
        is_ratio=is_ratio,
        oos_sharpe_ratio=oos_sharpe_ratio,
        n_trials=n_trials,
    )
    return engine.run(
        ohlcv_df, base_params, asset_config,
        balance=balance, risk_pct=risk_pct,
        symbol=symbol, log_fn=log_fn,
    )
