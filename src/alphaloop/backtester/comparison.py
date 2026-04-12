"""
backtester/comparison.py — v3 vs v4 pipeline comparison on historical data.

Runs the same backtest data through both the legacy v3 signal function
(make_signal_fn) and the v4 PipelineOrchestrator, then compares outcomes.

Usage:
    from alphaloop.backtester.comparison import run_comparison
    result = await run_comparison(symbol="XAUUSD", days=90)
    print(result.summary())
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import numpy as np

from alphaloop.backtester.engine import BacktestEngine, BacktestResult
from alphaloop.backtester.params import BacktestParams
from alphaloop.backtester.runner import make_signal_fn
from alphaloop.core.setup_types import normalize_pipeline_setup_type
from alphaloop.core.types import TradeDirection
from alphaloop.pipeline.types import CandidateSignal, CycleOutcome
from alphaloop.pipeline.orchestrator import PipelineOrchestrator
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.risk_gate import RiskGateRunner
from alphaloop.trading.strategy_loader import (
    build_algorithmic_params,
    build_strategy_resolution_input,
    resolve_strategy_setup_family,
)

logger = logging.getLogger(__name__)


def _comparison_backtest_params(
    params: BacktestParams | None,
    filters: list[str],
) -> BacktestParams:
    """Build a spec-consistent baseline when comparison runs without an explicit strategy."""
    if params is not None:
        return params

    from alphaloop.backtester.runner import _base_backtest_params

    return _base_backtest_params(
        signal_mode="algo_ai",
        signal_rules=None,
        signal_logic="AND",
        signal_auto=False,
        tools=filters,
        source="comparison",
    )


def _resolve_comparison_setup_type(params: BacktestParams, setup_label: str | None) -> str:
    """Resolve comparison setup type from the typed strategy contract first."""
    raw_label = str(setup_label or "").strip().lower()
    source_map = {
        "ema_crossover": "pullback",
        "macd_crossover": "continuation",
        "rsi_reversal": "reversal",
        "bollinger_breakout": "range_bounce",
        "adx_trend": "continuation",
        "bos_confirm": "breakout",
    }
    if raw_label in source_map:
        return normalize_pipeline_setup_type(source_map[raw_label])

    strategy_like = build_strategy_resolution_input(params, tools=params.tools)
    family = resolve_strategy_setup_family(strategy_like)
    if family:
        return normalize_pipeline_setup_type(family)
    return normalize_pipeline_setup_type(raw_label)


def _resolve_comparison_signal_sources(params: BacktestParams, setup_label: str | None) -> list[str]:
    """Resolve canonical signal sources for comparison telemetry."""
    raw_label = str(setup_label or "").strip().lower()
    if raw_label in {
        "ema_crossover",
        "macd_crossover",
        "rsi_reversal",
        "bollinger_breakout",
        "adx_trend",
        "bos_confirm",
    }:
        return [raw_label]

    strategy_like = build_strategy_resolution_input(params, tools=params.tools)
    resolved_params = build_algorithmic_params(strategy_like)
    sources = [
        str(item.get("source") or "").strip().lower()
        for item in (resolved_params.get("signal_rules") or [])
        if isinstance(item, dict) and str(item.get("source") or "").strip()
    ]
    if sources:
        return sources

    family = resolve_strategy_setup_family(strategy_like)
    if family:
        return [family]
    if raw_label:
        return [raw_label]
    return ["backtest"]


# ---------------------------------------------------------------------------
# Indicator helpers (reused from runner.py)
# ---------------------------------------------------------------------------

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=np.float64)
    if len(arr) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = np.mean(arr[:period])
    for j in range(period, len(arr)):
        out[j] = alpha * arr[j] + (1 - alpha) * out[j - 1]
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(closes), 50.0, dtype=np.float64)
    if len(closes) < period + 1:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for j in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[j]) / period
        avg_loss = (avg_loss * (period - 1) + losses[j]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        out[j + 1] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _atr(highs, lows, closes, period=14) -> np.ndarray:
    out = np.full(len(closes), np.nan, dtype=np.float64)
    if len(closes) < period + 1:
        return out
    trs = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    trs = np.concatenate([[highs[0] - lows[0]], trs])
    out[period - 1] = np.mean(trs[:period])
    alpha = 1.0 / period
    for j in range(period, len(trs)):
        out[j] = alpha * trs[j] + (1 - alpha) * out[j - 1]
    return out


# ---------------------------------------------------------------------------
# Mock context builder (converts bar data to pipeline-compatible context)
# ---------------------------------------------------------------------------

def _build_bar_context(
    i: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timestamps: list[datetime] | None,
    ema200_arr: np.ndarray,
    rsi_arr: np.ndarray,
    atr_arr: np.ndarray,
    symbol: str = "XAUUSD",
) -> SimpleNamespace:
    """Build a lightweight context object from bar data for the pipeline."""
    price = closes[i]
    atr_val = float(atr_arr[i]) if not np.isnan(atr_arr[i]) else 1.0
    atr_pct = atr_val / price if price > 0 else 0.003

    ts = timestamps[i] if timestamps else datetime.now(timezone.utc)
    hour_utc = ts.hour if isinstance(ts, datetime) else 12
    weekday = ts.weekday() if isinstance(ts, datetime) else 2

    # Simple session scoring
    is_weekend = weekday >= 5
    if 13 <= hour_utc < 16:
        session_score = 1.0
        session_name = "Overlap"
    elif 13 <= hour_utc < 21:
        session_score = 0.85
        session_name = "NY"
    elif 7 <= hour_utc < 16:
        session_score = 0.80
        session_name = "London"
    elif 4 <= hour_utc < 7:
        session_score = 0.40
        session_name = "Asia Late"
    else:
        session_score = 0.20
        session_name = "Asia Early"

    # Choppiness Index (simplified)
    ci = 50.0
    adx = 25.0

    ema200_val = float(ema200_arr[i]) if not np.isnan(ema200_arr[i]) else None
    rsi_val = float(rsi_arr[i])

    ctx = SimpleNamespace(
        symbol=symbol,
        trade_direction="",
        pip_size=0.01,
        indicators={
            "M15": {
                "ema200": ema200_val,
                "atr": atr_val,
                "choppiness": ci,
                "adx": adx,
                "rsi": rsi_val,
                "bos": None,
                "fvg": None,
                "vwap": None,
                "swing_structure": None,
                "bb_pct_b": None,
                "fast_fingers": None,
                "tick_jump_atr": 0.3,
                "liq_vacuum": {"bar_range_atr": 1.0, "body_pct": 60},
                "median_spread": 1.5,
            },
            "H1": {
                "atr_pct": atr_pct,
            },
        },
        session=SimpleNamespace(
            is_weekend=is_weekend,
            score=session_score,
            name=session_name,
        ),
        price=SimpleNamespace(
            bid=price - 0.5,
            ask=price + 0.5,
            spread=1.0,
            time=datetime.now(timezone.utc),  # Use "now" so stale check passes in backtest
        ),
        news=[],
        dxy=None,
        sentiment=None,
        open_trades={},
        risk_monitor=SimpleNamespace(
            _kill_switch_active=False,
            _open_risk_usd=0,
            account_balance=10000,
        ),
        df=[None] * 500,  # list with len() support for MarketGate bars check
    )
    return ctx


# ---------------------------------------------------------------------------
# V4 signal function adapter
# ---------------------------------------------------------------------------

def make_v4_signal_fn(
    params: BacktestParams,
    filters: list[str],
    symbol: str = "XAUUSD",
):
    """
    Create a backtest-compatible signal function that uses the v4 pipeline.

    Returns the same tuple format as make_signal_fn:
        (direction, entry, sl, tp1, tp2, setup_type, confidence) or None
    """
    _cache: dict = {}

    # Build a lightweight orchestrator (no AI, no risk monitor)
    # Lower thresholds for backtest since we don't have full tool suite
    scorer = ConvictionScorer()
    scorer._base_thresholds = {"strong_entry": 60.0, "min_entry": 40.0}

    orchestrator = PipelineOrchestrator(
        market_gate=MarketGate(
            stale_bar_seconds=9999,  # disable stale check for backtest
            min_bars_required=50,
        ),
        regime_classifier=RegimeClassifier(),
        invalidator=StructuralInvalidator(cfg={
            "sl_min_points": 50,
            "sl_max_points": 50000,  # Gold SL in pips can be very large
            "rr_hard_min": 0.8,
            "rr_soft_min": 1.0,     # Backtest uses strategy-defined R:R
        }),
        # No quality tools in backtest (simplified) — thresholds lowered accordingly
        conviction_scorer=scorer,
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
    )

    # Reuse the v3 signal function for generating the raw signal
    # The v4 pipeline then validates/scores it
    v3_fn = make_signal_fn(params, filters)

    async def signal_fn(
        i: int,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        _filters: list[str],
        timestamps: list | np.ndarray | None = None,
    ) -> tuple | None:
        # Pre-compute indicators once
        cid = id(closes)
        if cid not in _cache:
            _cache[cid] = {
                "ema200": _ema(closes, 200),
                "rsi": _rsi(closes, params.rsi_period),
                "atr": _atr(
                    highs.astype(np.float64),
                    lows.astype(np.float64),
                    closes.astype(np.float64),
                    14,
                ),
            }
        ind = _cache[cid]

        warmup = max(params.ema_slow + 2, 200)
        if i < warmup:
            return None

        # Build context for v4 pipeline
        ctx = _build_bar_context(
            i, opens, highs, lows, closes, timestamps,
            ind["ema200"], ind["rsi"], ind["atr"],
            symbol=symbol,
        )

        # Use v3 signal engine to generate the raw signal candidate
        v3_sig = await v3_fn(i, opens, highs, lows, closes, _filters, timestamps=timestamps)
        if v3_sig is None:
            return None

        direction, entry, sl, tp1, tp2, setup_type, conf = v3_sig

        # Build a signal generator that returns the v3 signal as a CandidateSignal
        async def gen_signal(context, regime):
            rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
            resolved_setup_type = _resolve_comparison_setup_type(params, setup_type)
            return CandidateSignal(
                direction="BUY" if direction == TradeDirection.BUY else "SELL",
                setup_type=resolved_setup_type,
                entry_zone=(entry - 0.5, entry + 0.5),
                stop_loss=sl,
                take_profit=[tp1] + ([tp2] if tp2 else []),
                raw_confidence=conf,
                rr_ratio=rr,
                signal_sources=_resolve_comparison_signal_sources(params, setup_type),
                reasoning="backtest signal",
                regime_at_generation=regime.regime,
            )

        # Run through v4 pipeline
        result = await orchestrator.run(
            ctx, gen_signal, symbol=symbol, mode="algo_only",
        )

        if result.outcome == CycleOutcome.TRADE_OPENED:
            return v3_sig  # v4 approved → execute
        return None  # v4 rejected/held → skip

    return signal_fn


# ---------------------------------------------------------------------------
# Comparison result
# ---------------------------------------------------------------------------

@dataclass
class ComparisonResult:
    """Side-by-side metrics from v3 and v4 backtest runs."""

    v3: dict = field(default_factory=dict)
    v4: dict = field(default_factory=dict)
    v3_trades: int = 0
    v4_trades: int = 0
    v3_blocked_v4_took: int = 0  # trades v3 blocked but v4 would take
    v4_blocked_v3_took: int = 0  # trades v4 blocked but v3 would take

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "  V3 vs V4 Pipeline Comparison",
            "=" * 60,
            "",
            f"{'Metric':<25} {'V3':>12} {'V4':>12} {'Delta':>12}",
            "-" * 60,
        ]
        for key in ["total_trades", "win_rate", "sharpe", "max_dd_pct", "total_pnl", "expectancy_r"]:
            v3_val = self.v3.get(key, 0) or 0
            v4_val = self.v4.get(key, 0) or 0
            delta = v4_val - v3_val if isinstance(v4_val, (int, float)) and isinstance(v3_val, (int, float)) else "—"
            lines.append(f"{key:<25} {str(v3_val):>12} {str(v4_val):>12} {str(delta):>12}")
        lines.append("-" * 60)
        lines.append(f"{'v3 took, v4 blocked':<25} {self.v4_blocked_v3_took:>12}")
        lines.append(f"{'v4 took, v3 blocked':<25} {self.v3_blocked_v4_took:>12}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main comparison runner
# ---------------------------------------------------------------------------

async def run_comparison(
    symbol: str = "XAUUSD",
    days: int = 90,
    balance: float = 10_000.0,
    risk_pct: float = 0.01,
    params: BacktestParams | None = None,
    filters: list[str] | None = None,
    timeframe: str = "1h",
) -> ComparisonResult:
    """
    Run the same data through v3 and v4 pipelines and compare results.

    Args:
        symbol: Trading symbol
        days: Days of historical data
        balance: Starting balance
        risk_pct: Risk per trade
        params: Strategy parameters (defaults if None)
        filters: Active filters
        timeframe: Data timeframe

    Returns:
        ComparisonResult with side-by-side metrics
    """
    from alphaloop.backtester.runner import _fetch_data

    if filters is None:
        filters = [
            "session_filter", "volatility_filter", "ema200_filter",
        ]

    params = _comparison_backtest_params(params, filters)

    logger.info(
        "[Comparison] Starting v3 vs v4 comparison: %s %dd %s",
        symbol, days, timeframe,
    )

    # Fetch data
    opens, highs, lows, closes, timestamps = await _fetch_data(
        symbol, days=days, run_id="comparison", timeframe=timeframe,
    )
    logger.info("[Comparison] Loaded %d bars", len(closes))

    engine = BacktestEngine()

    # --- Run v3 ---
    v3_fn = make_signal_fn(params, filters)
    v3_result = await engine.run(
        symbol=symbol,
        opens=opens, highs=highs, lows=lows, closes=closes,
        timestamps=timestamps,
        balance=balance,
        risk_pct=risk_pct,
        filters=filters,
        signal_fn=v3_fn,
    )
    logger.info("[Comparison] v3: %s", v3_result.summary())

    # --- Run v4 ---
    v4_fn = make_v4_signal_fn(params, filters, symbol=symbol)
    v4_result = await engine.run(
        symbol=symbol,
        opens=opens, highs=highs, lows=lows, closes=closes,
        timestamps=timestamps,
        balance=balance,
        risk_pct=risk_pct,
        filters=filters,
        signal_fn=v4_fn,
    )
    logger.info("[Comparison] v4: %s", v4_result.summary())

    # --- Compare ---
    v3_bars = {t.bar_index for t in v3_result.closed_trades}
    v4_bars = {t.bar_index for t in v4_result.closed_trades}

    comp = ComparisonResult(
        v3=v3_result.summary(),
        v4=v4_result.summary(),
        v3_trades=len(v3_result.closed_trades),
        v4_trades=len(v4_result.closed_trades),
        v3_blocked_v4_took=len(v4_bars - v3_bars),
        v4_blocked_v3_took=len(v3_bars - v4_bars),
    )

    logger.info("\n%s", comp.summary())
    return comp
