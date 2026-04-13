"""
Multi-asset configuration for the trading bot.

Supported asset classes: spot_metal, crypto, forex_major, forex_minor, index, stock
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AssetConfig(BaseModel):
    """Per-symbol trading parameters."""

    # Identity
    symbol: str = "XAUUSD"
    display_name: str = "Gold"
    asset_class: str = "spot_metal"
    mt5_symbol: str = "XAUUSDm"

    # Session preferences
    best_sessions: list[str] = ["london_ny_overlap", "ny_session"]
    min_session_score: float = 0.70
    avoid_sessions: list[str] = ["weekend", "asia_early"]

    # Pip / point values
    pip_value_per_lot: float = 1.0
    pip_size: float = 0.1
    min_lot: float = 0.01
    max_lot: float = 10.0
    lot_step: float = 0.01

    # Stop loss / take profit
    sl_atr_mult: float = 1.5
    tp1_rr: float = 1.5
    tp2_rr: float = 2.5
    sl_min_points: float = 150.0
    sl_max_points: float = 500.0

    # Entry
    entry_zone_atr_mult: float = 0.25
    min_confidence: float = 0.70
    min_rr_ratio: float = 1.50

    # Indicators
    ema_fast: int = 21
    ema_slow: int = 55
    ema_trend: int = 200
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    rsi_extreme_ob: float = 75.0
    rsi_extreme_os: float = 25.0
    atr_period: int = 14

    # Spread / volatility limits
    max_spread_points: float = 30.0
    max_volatility_atr_mult: float = 2.5

    # TP1 partial close
    tp1_close_pct: float = 0.60

    # Backtester
    max_param_change_pct: float = 0.15

    # Correlation filter
    use_dxy_filter: bool = False
    correlation_symbol: Optional[str] = None

    # AI context
    ai_context: str = ""

    # Trailing SL defaults (used by TrailingConfig.from_params as fallback)
    trail_atr_mult: float = 1.5
    trail_pips: float = 200.0       # symbol-specific — distance in pips for fixed_pips mode
    trail_activation_rr: float = 1.0
    trail_step_min_pips: float = 5.0

    # Per-timeframe construction calibration (populated per asset below)
    default_params_by_timeframe: dict[str, dict[str, object]] = {}


# ---------------------------------------------------------------------------
# TF calibration helper
# ---------------------------------------------------------------------------
def _tf(
    sl_min: float,
    sl_max: float,
    buf: float,
    tp2: float,
    zone: float,
    sl_atr: float,
    tp1: float,
    *,
    max_atr_pct: float = 0.0,
    min_atr_pct: float = 0.0,
    adx_thresh: float = 0.0,
    fvg_min_atr: float = 0.0,
    liq_spike: float = 0.0,
    tick_atr: float = 0.0,
    vwap_band: float = 0.0,
) -> dict[str, object]:
    """Build one timeframe's calibration dict for an asset."""
    d: dict[str, object] = {
        "sl_min_points": sl_min,
        "sl_max_points": sl_max,
        "sl_buffer_atr": buf,
        "tp2_rr": tp2,
        "entry_zone_atr_mult": zone,
        "sl_atr_mult": sl_atr,
        "tp1_rr": tp1,
    }
    tools_cfg: dict[str, dict[str, object]] = {}
    if max_atr_pct:
        tools_cfg.setdefault("volatility_filter", {})["max_atr_pct"] = max_atr_pct
    if min_atr_pct:
        tools_cfg.setdefault("volatility_filter", {})["min_atr_pct"] = min_atr_pct
    if adx_thresh:
        tools_cfg["adx_filter"] = {"adx_threshold": adx_thresh}
    if fvg_min_atr:
        tools_cfg["fvg_guard"] = {"fvg_min_atr": fvg_min_atr}
    if liq_spike:
        tools_cfg["liq_vacuum_guard"] = {"spike_mult": liq_spike}
    if tick_atr:
        tools_cfg["tick_jump_guard"] = {"tick_jump_atr_max": tick_atr}
    if vwap_band:
        tools_cfg["vwap_guard"] = {"vwap_band_atr": vwap_band}
    if tools_cfg:
        d["tools_config"] = tools_cfg
    return d


# --- Per-asset TF calibration tables ---
_XAUUSD_TF = {
    "M1":  _tf(30, 150, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.008, min_atr_pct=0.001),
    "M5":  _tf(60, 250, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.015, min_atr_pct=0.002),
    "M15": _tf(150, 500, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.03, min_atr_pct=0.003),
    "M30": _tf(200, 600, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.04, min_atr_pct=0.004),
    "H1":  _tf(250, 800, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.06, min_atr_pct=0.005),
    "H4":  _tf(350, 1200, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.08, min_atr_pct=0.006),
    "D1":  _tf(500, 2000, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.12, min_atr_pct=0.008),
}

_XAGUSD_TF = {
    "M1":  _tf(40, 200, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.010),
    "M5":  _tf(80, 350, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.020),
    "M15": _tf(200, 800, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.040),
    "M30": _tf(250, 900, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.050),
    "H1":  _tf(300, 1100, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.070),
    "H4":  _tf(450, 1500, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.090),
    "D1":  _tf(600, 2500, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.140),
}

_BTCUSD_TF = {
    "M1":  _tf(100, 1000, 0.05, 2.5, 0.15, 1.0, 1.0, max_atr_pct=0.010),
    "M5":  _tf(200, 2000, 0.08, 2.5, 0.20, 1.2, 1.2, max_atr_pct=0.020),
    "M15": _tf(500, 5000, 0.15, 3.0, 0.25, 1.5, 1.5, max_atr_pct=0.040),
    "M30": _tf(600, 5500, 0.15, 3.0, 0.25, 1.8, 1.5, max_atr_pct=0.050),
    "H1":  _tf(800, 7000, 0.20, 3.5, 0.30, 2.0, 2.0, max_atr_pct=0.070),
    "H4":  _tf(1200, 10000, 0.20, 3.5, 0.35, 2.5, 2.0, max_atr_pct=0.100),
    "D1":  _tf(2000, 15000, 0.25, 4.0, 0.40, 3.0, 2.5, max_atr_pct=0.150),
}

_ETHUSD_TF = {
    "M1":  _tf(30, 300, 0.05, 2.5, 0.15, 1.0, 1.0, max_atr_pct=0.012),
    "M5":  _tf(60, 600, 0.08, 2.5, 0.20, 1.2, 1.2, max_atr_pct=0.025),
    "M15": _tf(150, 1500, 0.15, 3.0, 0.25, 1.5, 1.5, max_atr_pct=0.050),
    "M30": _tf(200, 2000, 0.15, 3.0, 0.25, 1.8, 1.5, max_atr_pct=0.060),
    "H1":  _tf(300, 3000, 0.20, 3.5, 0.30, 2.0, 2.0, max_atr_pct=0.080),
    "H4":  _tf(500, 5000, 0.20, 3.5, 0.35, 2.5, 2.0, max_atr_pct=0.120),
    "D1":  _tf(800, 8000, 0.25, 4.0, 0.40, 3.0, 2.5, max_atr_pct=0.170),
}

_EURUSD_TF = {
    "M1":  _tf(5, 30, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.003),
    "M5":  _tf(10, 50, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.005),
    "M15": _tf(20, 100, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.010),
    "M30": _tf(30, 120, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.015),
    "H1":  _tf(40, 200, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.020),
    "H4":  _tf(60, 350, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.030),
    "D1":  _tf(100, 600, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.050),
}

_GBPUSD_TF = {
    "M1":  _tf(6, 35, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.004),
    "M5":  _tf(12, 60, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.006),
    "M15": _tf(25, 120, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.012),
    "M30": _tf(35, 150, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.018),
    "H1":  _tf(50, 250, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.025),
    "H4":  _tf(80, 400, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.035),
    "D1":  _tf(120, 700, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.060),
}

_USDJPY_TF = {
    "M1":  _tf(5, 30, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.003),
    "M5":  _tf(10, 50, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.005),
    "M15": _tf(20, 100, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.010),
    "M30": _tf(30, 130, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.015),
    "H1":  _tf(40, 200, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.020),
    "H4":  _tf(60, 350, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.030),
    "D1":  _tf(100, 600, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.050),
}

_AUDUSD_TF = {
    "M1":  _tf(4, 25, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.003),
    "M5":  _tf(8, 40, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.005),
    "M15": _tf(15, 80, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.010),
    "M30": _tf(20, 100, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.012),
    "H1":  _tf(30, 150, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.018),
    "H4":  _tf(50, 300, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.028),
    "D1":  _tf(80, 500, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.045),
}

_US30_TF = {
    "M1":  _tf(40, 200, 0.05, 2.0, 0.15, 1.0, 1.0, max_atr_pct=0.005),
    "M5":  _tf(80, 400, 0.08, 2.0, 0.20, 1.2, 1.2, max_atr_pct=0.008),
    "M15": _tf(200, 1000, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.015),
    "M30": _tf(250, 1200, 0.15, 2.5, 0.25, 1.8, 1.5, max_atr_pct=0.020),
    "H1":  _tf(350, 1500, 0.20, 3.0, 0.30, 2.0, 2.0, max_atr_pct=0.030),
    "H4":  _tf(500, 2500, 0.20, 3.0, 0.35, 2.5, 2.0, max_atr_pct=0.045),
    "D1":  _tf(800, 4000, 0.25, 3.5, 0.40, 3.0, 2.5, max_atr_pct=0.070),
}

_NAS100_TF = {
    "M1":  _tf(60, 300, 0.05, 2.5, 0.15, 1.0, 1.0, max_atr_pct=0.006),
    "M5":  _tf(120, 600, 0.08, 2.5, 0.20, 1.2, 1.2, max_atr_pct=0.010),
    "M15": _tf(300, 1500, 0.15, 3.0, 0.25, 1.5, 1.5, max_atr_pct=0.020),
    "M30": _tf(350, 1800, 0.15, 3.0, 0.25, 1.8, 1.5, max_atr_pct=0.025),
    "H1":  _tf(500, 2500, 0.20, 3.5, 0.30, 2.0, 2.0, max_atr_pct=0.035),
    "H4":  _tf(800, 4000, 0.20, 3.5, 0.35, 2.5, 2.0, max_atr_pct=0.055),
    "D1":  _tf(1200, 6000, 0.25, 4.0, 0.40, 3.0, 2.5, max_atr_pct=0.080),
}

_ASSET_TF_MAP: dict[str, dict[str, dict[str, object]]] = {
    "XAUUSD": _XAUUSD_TF, "XAGUSD": _XAGUSD_TF, "BTCUSD": _BTCUSD_TF,
    "ETHUSD": _ETHUSD_TF, "EURUSD": _EURUSD_TF, "GBPUSD": _GBPUSD_TF,
    "USDJPY": _USDJPY_TF, "AUDUSD": _AUDUSD_TF, "US30": _US30_TF,
    "NAS100": _NAS100_TF,
}

# ── Asset library ─────────────────────────────────────────────────────────────

ASSETS: dict[str, AssetConfig] = {
    "XAUUSD": AssetConfig(
        symbol="XAUUSD",
        display_name="Gold",
        asset_class="spot_metal",
        mt5_symbol="XAUUSDm",
        best_sessions=["london_ny_overlap", "ny_session", "london_session"],
        pip_value_per_lot=1.0,
        pip_size=0.1,
        sl_atr_mult=1.5,
        sl_min_points=150.0,
        sl_max_points=500.0,
        max_spread_points=30.0,
        use_dxy_filter=True,
        correlation_symbol="DX-Y.NYB",
        # Trail: pip_size=0.1 → 200 pips=$20, activation at 1R, step 5 pips=$0.50
        trail_atr_mult=1.5,
        trail_pips=200.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=5.0,
        default_params_by_timeframe=_XAUUSD_TF,
        ai_context=(
            "XAUUSD specific: Watch for liquidity sweeps at round numbers ($2300, $2350). "
            "NY open (13:00 UTC) often creates the strongest moves. "
            "DXY inverse correlation is strong — dollar weakness = gold strength. "
            "Avoid trading 30min before/after NFP, CPI, Fed decisions."
        ),
    ),
    "XAGUSD": AssetConfig(
        symbol="XAGUSD",
        display_name="Silver",
        asset_class="spot_metal",
        mt5_symbol="XAGUSDm",
        best_sessions=["london_ny_overlap", "ny_session"],
        pip_value_per_lot=50.0,
        pip_size=0.001,
        sl_atr_mult=1.8,
        sl_min_points=200.0,
        sl_max_points=800.0,
        max_spread_points=50.0,
        use_dxy_filter=True,
        # Trail: pip_size=0.001 → 300 pips=$0.30, more volatile than gold
        trail_atr_mult=1.8,
        trail_pips=300.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=10.0,
        default_params_by_timeframe=_XAGUSD_TF,
        ai_context=(
            "XAGUSD: More volatile than gold, wider spreads. "
            "Follows gold but with amplified moves (gold/silver ratio matters)."
        ),
    ),
    "BTCUSD": AssetConfig(
        symbol="BTCUSD",
        display_name="Bitcoin",
        asset_class="crypto",
        mt5_symbol="BTCUSDm",
        best_sessions=["london_ny_overlap", "ny_session", "london_session", "asia_late"],
        min_session_score=0.40,
        pip_value_per_lot=1.0,
        pip_size=1.0,
        sl_atr_mult=2.0,
        tp2_rr=3.0,
        sl_min_points=500.0,
        sl_max_points=5000.0,
        max_spread_points=200.0,
        # Trail: pip_size=1.0 → 1500 pips=$1500 ≈ 1R, wider trail for BTC volatility
        trail_atr_mult=2.0,
        trail_pips=1500.0,
        trail_activation_rr=1.5,
        trail_step_min_pips=50.0,
        default_params_by_timeframe=_BTCUSD_TF,
        ai_context=(
            "BTCUSD specific: 24/7 market but most volatility during US hours. "
            "Watch for whale manipulation and liquidation cascades at key levels. "
            "Strong correlation with NASDAQ/tech stocks."
        ),
    ),
    "ETHUSD": AssetConfig(
        symbol="ETHUSD",
        display_name="Ethereum",
        asset_class="crypto",
        mt5_symbol="ETHUSDm",
        best_sessions=["london_ny_overlap", "ny_session", "london_session"],
        min_session_score=0.40,
        pip_value_per_lot=1.0,
        pip_size=0.1,
        sl_atr_mult=2.0,
        tp2_rr=3.0,
        sl_min_points=300.0,
        sl_max_points=3000.0,
        max_spread_points=150.0,
        # Trail: pip_size=0.1 → 500 pips=$50, wider than gold due to ETH volatility
        trail_atr_mult=2.0,
        trail_pips=500.0,
        trail_activation_rr=1.5,
        trail_step_min_pips=20.0,
        default_params_by_timeframe=_ETHUSD_TF,
        ai_context=(
            "ETHUSD: Follows BTC with beta amplification. "
            "Gas fees and network activity affect sentiment."
        ),
    ),
    "EURUSD": AssetConfig(
        symbol="EURUSD",
        display_name="Euro/Dollar",
        asset_class="forex_major",
        mt5_symbol="EURUSD",
        best_sessions=["london_session", "london_ny_overlap"],
        pip_value_per_lot=10.0,
        pip_size=0.0001,
        sl_min_points=100.0,
        sl_max_points=300.0,
        max_spread_points=3.0,
        use_dxy_filter=True,
        correlation_symbol="DX-Y.NYB",
        # Trail: pip_size=0.0001 → 150 pips=$0.015, step 3 pips
        trail_atr_mult=1.5,
        trail_pips=150.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=3.0,
        default_params_by_timeframe=_EURUSD_TF,
        ai_context="EURUSD: Most liquid forex pair. Best during London/NY overlap.",
    ),
    "GBPUSD": AssetConfig(
        symbol="GBPUSD",
        display_name="Cable",
        asset_class="forex_major",
        mt5_symbol="GBPUSD",
        best_sessions=["london_session", "london_ny_overlap"],
        pip_value_per_lot=10.0,
        pip_size=0.0001,
        sl_min_points=120.0,
        sl_max_points=350.0,
        max_spread_points=5.0,
        use_dxy_filter=True,
        # Trail: wider than EURUSD due to higher volatility
        trail_atr_mult=1.5,
        trail_pips=200.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=5.0,
        default_params_by_timeframe=_GBPUSD_TF,
        ai_context="GBPUSD (Cable): Higher volatility than EURUSD.",
    ),
    "USDJPY": AssetConfig(
        symbol="USDJPY",
        display_name="Dollar/Yen",
        asset_class="forex_major",
        mt5_symbol="USDJPY",
        best_sessions=["london_ny_overlap", "ny_session", "asia_late"],
        min_session_score=0.40,
        pip_value_per_lot=9.0,
        pip_size=0.01,
        sl_min_points=100.0,
        sl_max_points=300.0,
        max_spread_points=3.0,
        # Trail: pip_size=0.01 → 150 pips=$1.50
        trail_atr_mult=1.5,
        trail_pips=150.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=3.0,
        default_params_by_timeframe=_USDJPY_TF,
        ai_context="USDJPY: Driven by US-Japan yield differential.",
    ),
    "AUDUSD": AssetConfig(
        symbol="AUDUSD",
        display_name="Aussie/Dollar",
        asset_class="forex_major",
        mt5_symbol="AUDUSD",
        best_sessions=["london_ny_overlap", "ny_session", "asia_late"],
        min_session_score=0.40,
        pip_value_per_lot=10.0,
        pip_size=0.0001,
        sl_min_points=80.0,
        sl_max_points=250.0,
        max_spread_points=4.0,
        use_dxy_filter=True,
        trail_atr_mult=1.5,
        trail_pips=150.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=3.0,
        default_params_by_timeframe=_AUDUSD_TF,
        ai_context="AUDUSD: Commodity currency — correlates with gold and iron ore.",
    ),
    "US30": AssetConfig(
        symbol="US30",
        display_name="Dow Jones",
        asset_class="index",
        mt5_symbol="US30",
        best_sessions=["ny_session", "london_ny_overlap"],
        pip_value_per_lot=1.0,
        pip_size=1.0,
        sl_min_points=200.0,
        sl_max_points=1000.0,
        max_spread_points=50.0,
        # Trail: pip_size=1.0 → 300 pips=$300, indices need wider trail
        trail_atr_mult=1.8,
        trail_pips=300.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=20.0,
        default_params_by_timeframe=_US30_TF,
        ai_context="US30 (Dow Jones): Driven by earnings, Fed policy, economic data.",
    ),
    "NAS100": AssetConfig(
        symbol="NAS100",
        display_name="Nasdaq 100",
        asset_class="index",
        mt5_symbol="NAS100",
        best_sessions=["ny_session", "london_ny_overlap"],
        pip_value_per_lot=1.0,
        pip_size=1.0,
        sl_atr_mult=1.8,
        tp2_rr=3.0,
        sl_min_points=300.0,
        sl_max_points=1500.0,
        max_spread_points=100.0,
        # Trail: pip_size=1.0 → 400 pips=$400, more volatile than US30
        trail_atr_mult=2.0,
        trail_pips=400.0,
        trail_activation_rr=1.0,
        trail_step_min_pips=30.0,
        default_params_by_timeframe=_NAS100_TF,
        ai_context="NAS100: Tech-heavy index, more volatile than Dow.",
    ),
}


def get_asset_config(symbol: str) -> AssetConfig:
    """Returns the AssetConfig for a symbol, with broker suffix stripping."""
    if symbol in ASSETS:
        return ASSETS[symbol]

    clean = re.sub(r'[mM]$', '', symbol).replace(".pro", "").upper()
    if clean in ASSETS:
        return ASSETS[clean]

    logger.critical(
        f"Symbol '{symbol}' not in asset library — using generic config with "
        f"WRONG pip/contract values. Add it to config/assets.py before trading live."
    )
    return AssetConfig(
        symbol=symbol,
        display_name=symbol,
        asset_class="unknown",
        mt5_symbol=symbol,
        ai_context=f"Trading {symbol}. Apply standard technical analysis principles.",
    )
