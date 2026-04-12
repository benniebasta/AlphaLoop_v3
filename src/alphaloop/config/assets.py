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
