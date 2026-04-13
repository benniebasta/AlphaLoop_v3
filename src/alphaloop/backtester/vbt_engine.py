"""
backtester/vbt_engine.py — vectorbt-based backtester using TradeConstructor.

Ensures backtest SL/TP logic is identical to the live trading path:
  1. Direction hypotheses from compute_direction() (same as AlgorithmicSignalEngine)
  2. SL derived from market structure via TradeConstructor (same as live)
  3. TP from R:R multiplication (same as live)
  4. No alternative SL/TP logic

Returns VBTBacktestResult with performance metrics and construction stats.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from alphaloop.config.assets import AssetConfig, get_asset_config
from alphaloop.data.indicators import (
    atr as compute_atr,
    ema,
    rsi as compute_rsi,
    macd as compute_macd,
    bollinger,
    adx as compute_adx,
    find_swing_highs_lows,
    detect_fvg,
    detect_bos,
)
from alphaloop.pipeline.construction import TradeConstructor
from alphaloop.pipeline.types import DirectionHypothesis
from alphaloop.signals.algorithmic import compute_direction
from alphaloop.trading.strategy_loader import (
    build_algorithmic_params,
    build_strategy_resolution_input,
    resolve_algorithmic_setup_tag,
    resolve_strategy_signal_logic,
    resolve_strategy_signal_rules,
    serialize_strategy_spec,
)

logger = logging.getLogger(__name__)


@dataclass
class VBTBacktestResult:
    """Complete result of a vectorbt-powered backtest."""

    # Performance
    total_return: float = 0.0
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # Trade stats
    trade_count: int = 0
    total_pnl: float = 0.0
    avg_rr: float = 0.0
    avg_sl_distance_pts: float = 0.0

    # Construction stats
    opportunities: int = 0            # direction hypotheses generated
    valid_constructed: int = 0        # trades successfully constructed
    skipped_by_reason: dict[str, int] = field(default_factory=dict)
    execution_rate: float = 0.0       # valid_constructed / opportunities

    # Equity
    equity_curve: list[float] = field(default_factory=list)

    # Error
    error: str | None = None


def _build_backtest_strategy_payload(params: dict[str, Any]) -> dict[str, Any]:
    """Build one canonical strategy payload for the vectorbt path."""
    payload = build_strategy_resolution_input(
        {
            "signal_mode": params.get("signal_mode"),
            "setup_family": params.get("setup_family"),
            "strategy_spec": params.get("strategy_spec"),
            "source": params.get("source"),
            "tools": params.get("tools"),
            "params": dict(params),
        }
    )
    payload["params"] = build_algorithmic_params(payload)
    payload["strategy_spec"] = serialize_strategy_spec(payload)
    return payload


def _resolve_backtest_setup_tag(params: dict[str, Any]) -> str:
    """Resolve the backtest hypothesis setup tag through the shared strategy contract."""
    return resolve_algorithmic_setup_tag(_build_backtest_strategy_payload(params))


def _configured_signal_rules(params: dict[str, Any]) -> list[dict]:
    """Resolve signal rules through the shared strategy contract."""
    return resolve_strategy_signal_rules(_build_backtest_strategy_payload(params), default_to_ema=True)


def _configured_signal_logic(params: dict[str, Any]) -> str:
    return resolve_strategy_signal_logic(_build_backtest_strategy_payload(params))


def run_vectorbt_backtest(
    ohlcv_df: pd.DataFrame,
    params: dict[str, Any],
    asset_config: AssetConfig | None = None,
    symbol: str = "XAUUSD",
    balance: float = 10_000.0,
    risk_pct: float = 0.01,
) -> VBTBacktestResult:
    """Run a backtest using vectorbt with the live TradeConstructor path.

    Parameters
    ----------
    ohlcv_df : pd.DataFrame
        OHLCV data with columns: open, high, low, close, volume, time.
    params : dict
        Strategy parameters (signal_rules, signal_logic, tp1_rr, tp2_rr, etc.).
    asset_config : AssetConfig | None
        Asset config. Loaded from symbol if None.
    symbol : str
        Trading symbol (for asset config lookup).
    balance : float
        Starting account balance.
    risk_pct : float
        Risk per trade as fraction of balance.

    Returns
    -------
    VBTBacktestResult
    """
    try:
        import vectorbt as vbt
    except ImportError:
        return VBTBacktestResult(error="vectorbt not installed")

    if asset_config is None:
        asset_config = get_asset_config(symbol)

    if len(ohlcv_df) < 60:
        return VBTBacktestResult(error=f"Insufficient data: {len(ohlcv_df)} bars (need 60+)")

    strategy_payload = _build_backtest_strategy_payload(params)
    resolved_params = strategy_payload["params"]

    # --- Resolve construction params through 5-layer precedence ---
    from alphaloop.trading.strategy_loader import resolve_construction_params
    backtest_tf = str(params.get("timeframe", "M15")).upper()
    _cp = resolve_construction_params(strategy_payload, backtest_tf, asset_config)

    # --- Build TradeConstructor ---
    tc = TradeConstructor(
        pip_size=asset_config.pip_size,
        sl_min_pts=_cp["sl_min_points"],
        sl_max_pts=_cp["sl_max_points"],
        tp1_rr=_cp["tp1_rr"],
        tp2_rr=_cp["tp2_rr"],
        entry_zone_atr_mult=_cp["entry_zone_atr_mult"],
        sl_buffer_atr=_cp["sl_buffer_atr"],
        sl_atr_mult=_cp["sl_atr_mult"],
    )

    # --- Compute indicators ---
    close = ohlcv_df["close"]
    ema_fast_period = int(resolved_params.get("ema_fast", 21))
    ema_slow_period = int(resolved_params.get("ema_slow", 55))

    ema_fast_s = ema(close, ema_fast_period)
    ema_slow_s = ema(close, ema_slow_period)
    rsi_s = compute_rsi(close, int(resolved_params.get("rsi_period", 14)))
    atr_s = compute_atr(ohlcv_df, 14)
    macd_data = compute_macd(close)
    macd_hist_s = macd_data["histogram"]
    bb_data = bollinger(close)
    bb_pctb_s = bb_data.get("pct_b_series")  # May not exist in all versions
    adx_data = compute_adx(ohlcv_df)

    signal_rules = resolve_strategy_signal_rules(strategy_payload, default_to_ema=True)
    signal_logic = resolve_strategy_signal_logic(strategy_payload)
    rsi_ob = float(resolved_params.get("rsi_ob", 70.0))
    rsi_os = float(resolved_params.get("rsi_os", 30.0))
    setup_tag = resolve_algorithmic_setup_tag(strategy_payload)

    # --- Bar-by-bar simulation ---
    n = len(ohlcv_df)
    entries = np.full(n, False)
    exits = np.full(n, False)
    entry_prices = np.full(n, np.nan)
    sl_prices = np.full(n, np.nan)
    tp_prices = np.full(n, np.nan)
    directions = np.full(n, 0)  # 1=BUY, -1=SELL

    opportunities = 0
    valid_constructed = 0
    skipped_reasons: dict[str, int] = {}
    sl_distances: list[float] = []
    rr_ratios: list[float] = []

    lookback = max(ema_slow_period, 55) + 5  # need enough bars for indicators

    in_trade = False
    trade_direction = 0
    trade_sl = 0.0
    trade_tp = 0.0

    for i in range(lookback, n):
        # If in a trade, check SL/TP hits on this bar
        if in_trade:
            bar_high = float(ohlcv_df["high"].iloc[i])
            bar_low = float(ohlcv_df["low"].iloc[i])

            if trade_direction == 1:  # BUY
                if bar_low <= trade_sl:
                    exits[i] = True
                    in_trade = False
                    continue
                if bar_high >= trade_tp:
                    exits[i] = True
                    in_trade = False
                    continue
            else:  # SELL
                if bar_high >= trade_sl:
                    exits[i] = True
                    in_trade = False
                    continue
                if bar_low <= trade_tp:
                    exits[i] = True
                    in_trade = False
                    continue
            continue  # still in trade, no new signals

        # --- Generate direction hypothesis ---
        cur_ema_fast = float(ema_fast_s.iloc[i]) if not pd.isna(ema_fast_s.iloc[i]) else None
        cur_ema_slow = float(ema_slow_s.iloc[i]) if not pd.isna(ema_slow_s.iloc[i]) else None
        prev_ema_fast = float(ema_fast_s.iloc[i - 1]) if not pd.isna(ema_fast_s.iloc[i - 1]) else None
        prev_ema_slow = float(ema_slow_s.iloc[i - 1]) if not pd.isna(ema_slow_s.iloc[i - 1]) else None
        cur_rsi = float(rsi_s.iloc[i]) if not pd.isna(rsi_s.iloc[i]) else None
        prev_rsi = float(rsi_s.iloc[i - 1]) if not pd.isna(rsi_s.iloc[i - 1]) else None
        cur_macd = float(macd_hist_s.iloc[i]) if not pd.isna(macd_hist_s.iloc[i]) else None
        prev_macd = float(macd_hist_s.iloc[i - 1]) if not pd.isna(macd_hist_s.iloc[i - 1]) else None
        cur_price = float(close.iloc[i])
        cur_atr = float(atr_s.iloc[i]) if not pd.isna(atr_s.iloc[i]) else 0

        # ADX
        cur_adx = float(adx_data.get("adx", 0)) if isinstance(adx_data.get("adx"), (int, float)) else None
        cur_plus_di = float(adx_data.get("plus_di", 0)) if isinstance(adx_data.get("plus_di"), (int, float)) else None
        cur_minus_di = float(adx_data.get("minus_di", 0)) if isinstance(adx_data.get("minus_di"), (int, float)) else None

        # BOS data
        bos_data = detect_bos(ohlcv_df.iloc[:i + 1], cur_atr, lookback=20)

        result = compute_direction(
            signal_rules=signal_rules,
            signal_logic=signal_logic,
            rsi_ob=rsi_ob,
            rsi_os=rsi_os,
            price=cur_price,
            ema_fast=cur_ema_fast,
            ema_slow=cur_ema_slow,
            prev_ema_fast=prev_ema_fast,
            prev_ema_slow=prev_ema_slow,
            rsi=cur_rsi,
            macd_hist=cur_macd,
            prev_macd_hist=prev_macd,
            bb_pct_b=None,  # bollinger pct_b series access varies by version
            adx=cur_adx,
            plus_di=cur_plus_di,
            minus_di=cur_minus_di,
            prev_rsi=prev_rsi,
            bos_swing_high=bos_data.get("swing_high"),
            bos_swing_low=bos_data.get("swing_low"),
        )

        if result is None:
            continue

        direction, confidence, reasoning = result
        opportunities += 1

        # --- Construct trade using live TradeConstructor ---
        # Build indicators dict matching what market_context produces
        swing_data = find_swing_highs_lows(ohlcv_df.iloc[:i + 1])
        fvg_data = detect_fvg(ohlcv_df.iloc[:i + 1], cur_atr)

        indicators = {
            "swing_highs": swing_data["swing_highs"],
            "swing_lows": swing_data["swing_lows"],
            "fvg": fvg_data,
            "atr": cur_atr,
        }

        from datetime import datetime, timezone
        hypothesis = DirectionHypothesis(
            direction=direction,
            confidence=confidence,
            setup_tag=setup_tag,
            reasoning=reasoning,
            source_names="+".join(r.get("source", "ema_crossover") for r in signal_rules),
            generated_at=datetime.now(timezone.utc),
        )

        # Use close as both bid and ask (backtest: no spread simulation here)
        construction = tc.construct(hypothesis, cur_price, cur_price, indicators, cur_atr)

        if construction.signal is None:
            reason = construction.rejection_reason or "unknown"
            # Categorize skip reason
            if "no valid SL" in reason:
                key = "no_structure"
            elif "too" in reason or "outside" in reason:
                key = "sl_out_of_bounds"
            else:
                key = reason[:30]
            skipped_reasons[key] = skipped_reasons.get(key, 0) + 1
            continue

        # --- Record entry ---
        valid_constructed += 1
        sig = construction.signal
        entries[i] = True
        entry_prices[i] = cur_price
        sl_prices[i] = sig.stop_loss
        tp_prices[i] = sig.take_profit[0]  # TP1
        directions[i] = 1 if direction == "BUY" else -1

        in_trade = True
        trade_direction = 1 if direction == "BUY" else -1
        trade_sl = sig.stop_loss
        trade_tp = sig.take_profit[0]

        sl_dist_pts = abs(cur_price - sig.stop_loss) / asset_config.pip_size
        sl_distances.append(sl_dist_pts)
        rr_ratios.append(sig.rr_ratio)

    # --- Compute performance from trade list ---
    trades_pnl: list[float] = []
    trades_pnl_r: list[float] = []
    equity = [balance]

    entry_idx_list = np.where(entries)[0]
    exit_idx_list = np.where(exits)[0]

    # Pair entries with exits
    trade_pairs = []
    j = 0
    for eidx in entry_idx_list:
        # Find the next exit after this entry
        while j < len(exit_idx_list) and exit_idx_list[j] <= eidx:
            j += 1
        if j < len(exit_idx_list):
            trade_pairs.append((eidx, exit_idx_list[j]))
            j += 1

    for entry_i, exit_i in trade_pairs:
        entry_p = entry_prices[entry_i]
        sl_p = sl_prices[entry_i]
        tp_p = tp_prices[entry_i]
        d = directions[entry_i]

        # Determine if SL or TP was hit
        exit_bar_high = float(ohlcv_df["high"].iloc[exit_i])
        exit_bar_low = float(ohlcv_df["low"].iloc[exit_i])

        risk = abs(entry_p - sl_p)
        if risk <= 0:
            continue

        lots = (balance * risk_pct) / (risk / asset_config.pip_size * asset_config.pip_value_per_lot) if risk > 0 else 0
        lots = max(asset_config.min_lot, min(lots, asset_config.max_lot))

        if d == 1:  # BUY
            if exit_bar_low <= sl_p:
                pnl = -(risk) * lots * (1 / asset_config.pip_size) * asset_config.pip_value_per_lot
                pnl_r = -1.0
            else:
                pnl = abs(tp_p - entry_p) * lots * (1 / asset_config.pip_size) * asset_config.pip_value_per_lot
                pnl_r = abs(tp_p - entry_p) / risk
        else:  # SELL
            if exit_bar_high >= sl_p:
                pnl = -(risk) * lots * (1 / asset_config.pip_size) * asset_config.pip_value_per_lot
                pnl_r = -1.0
            else:
                pnl = abs(entry_p - tp_p) * lots * (1 / asset_config.pip_size) * asset_config.pip_value_per_lot
                pnl_r = abs(entry_p - tp_p) / risk

        trades_pnl.append(pnl)
        trades_pnl_r.append(pnl_r)
        balance += pnl
        equity.append(balance)

    # --- Compute metrics ---
    trade_count = len(trades_pnl)
    if trade_count == 0:
        return VBTBacktestResult(
            opportunities=opportunities,
            valid_constructed=valid_constructed,
            skipped_by_reason=skipped_reasons,
            execution_rate=valid_constructed / max(opportunities, 1),
            equity_curve=equity,
        )

    wins = sum(1 for p in trades_pnl if p > 0)
    losses = sum(1 for p in trades_pnl if p <= 0)
    win_rate = wins / trade_count

    gross_profit = sum(p for p in trades_pnl if p > 0)
    gross_loss = abs(sum(p for p in trades_pnl if p < 0))
    profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else 99.0

    total_pnl = sum(trades_pnl)
    expectancy = round(sum(trades_pnl_r) / trade_count, 3)
    avg_rr = round(sum(rr_ratios) / len(rr_ratios), 3) if rr_ratios else 0.0
    avg_sl_dist = round(sum(sl_distances) / len(sl_distances), 1) if sl_distances else 0.0

    # Sharpe
    pnl_arr = np.array(trades_pnl, dtype=np.float64)
    std = float(np.std(pnl_arr, ddof=1)) if trade_count >= 10 else 0
    sharpe = round(float(np.mean(pnl_arr) / std * (252 ** 0.5)), 3) if std > 0 else None

    # Sortino
    downside = pnl_arr[pnl_arr < 0]
    down_std = float(np.std(downside, ddof=1)) if len(downside) >= 5 else 0
    sortino = round(float(np.mean(pnl_arr) / down_std * (252 ** 0.5)), 3) if down_std > 0 else None

    # Max drawdown
    eq_arr = np.array(equity, dtype=np.float64)
    peak = np.maximum.accumulate(eq_arr)
    dd = np.where(peak > 0, (eq_arr - peak) / peak, 0.0)
    max_dd = round(float(dd.min()) * 100, 2)

    total_return = round((equity[-1] / equity[0] - 1) * 100, 2)

    return VBTBacktestResult(
        total_return=total_return,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd,
        win_rate=round(win_rate, 3),
        profit_factor=profit_factor,
        expectancy=expectancy,
        trade_count=trade_count,
        total_pnl=round(total_pnl, 2),
        avg_rr=avg_rr,
        avg_sl_distance_pts=avg_sl_dist,
        opportunities=opportunities,
        valid_constructed=valid_constructed,
        skipped_by_reason=skipped_reasons,
        execution_rate=round(valid_constructed / max(opportunities, 1), 3),
        equity_curve=equity,
    )
