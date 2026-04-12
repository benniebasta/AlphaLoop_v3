"""Tunable backtest parameters — mutated by Optuna across generations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BacktestParams(BaseModel):
    """Strategy parameters that the optimizer can mutate."""

    # EMA periods
    ema_fast: int = 21
    ema_slow: int = 55

    # SL/TP as ATR multipliers
    sl_atr_mult: float = 2.0
    tp1_rr: float = 2.0       # TP1 = sl_atr_mult × tp1_rr
    tp2_rr: float = 4.0       # TP2 = sl_atr_mult × tp2_rr

    # RSI thresholds
    rsi_period: int = 14
    rsi_ob: float = 70.0      # overbought — block BUY above this
    rsi_os: float = 30.0      # oversold — block SELL below this

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Bollinger Bands
    bb_period: int = 20
    bb_std_dev: float = 2.0

    # ADX
    adx_period: int = 14
    adx_min_threshold: float = 20.0

    # Volume
    volume_ma_period: int = 20

    # Risk
    risk_pct: float = 0.01    # 1% per trade

    # Signal source configuration
    signal_rules: list[dict] = Field(default_factory=lambda: [{"source": "ema_crossover"}])
    signal_logic: str = "AND"   # "AND" | "OR" | "MAJORITY"
    signal_auto: bool = False    # let Optuna auto-pick sources + logic
    signal_mode: str = "algo_ai"
    setup_family: str = ""
    strategy_spec: dict[str, Any] = Field(default_factory=dict)
    tools: dict[str, bool] = Field(default_factory=dict)
    source: str = ""

    # Trailing stop loss
    trail_enabled: bool = False
    trail_type: str = "atr"           # "atr" | "fixed_pips"
    trail_atr_mult: float = 1.5       # SL distance = ATR × trail_atr_mult
    trail_pips: float = 200.0         # SL distance in pips (fixed_pips mode)
    trail_activation_rr: float = 1.0  # min R-profit before trail engages
    trail_step_min_pips: float = 5.0  # min SL improvement per cycle

    # Bounds for optimizer (not tuned directly, used by suggest())
    max_param_change_pct: float = 0.15  # max ±15% per generation
