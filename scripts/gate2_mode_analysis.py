"""Gate-2 mode comparison analysis — 241 bars, all 3 modes.

Runs the full pipeline on real BTC H1 bars (yfinance) and produces a
side-by-side funnel table for algo_only / algo_ai / ai_signal.

For algo_ai and ai_signal, an AI validator stub is injected:
  --ai-approval-rate  0.0 = AI rejects all signals (worst case)
  --ai-approval-rate  1.0 = AI approves all signals (best case)
  --ai-approval-rate  0.6 = AI approves 60% (default realistic estimate)

ai_signal mode uses soft veto (-0.15 conf) on AI reject instead of hard block.

Usage::

    python -m scripts.gate2_mode_analysis --symbol BTCUSD --bars 241
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd

logger = logging.getLogger("gate2_analysis")


# ─── reuse helpers from replay_live_cycles ──────────────────────────────────

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=1).mean()

def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    g = d.where(d > 0, 0.0).rolling(n, min_periods=1).mean()
    l = -d.where(d < 0, 0.0).rolling(n, min_periods=1).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=1).mean()

def _macd(close: pd.Series) -> dict:
    e12 = _ema(close, 12)
    e26 = _ema(close, 26)
    line = e12 - e26
    sig = _ema(line, 9)
    return {"line": line, "signal": sig, "hist": line - sig}

def _bb(close: pd.Series, n: int = 20, k: float = 2.0) -> dict:
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=1).std().fillna(0)
    upper = mid + k * std
    lower = mid - k * std
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return {"upper": upper, "mid": mid, "lower": lower, "pct_b": pct_b}

def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/n, adjust=False, min_periods=1).mean()
    pdi = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/n, adjust=False, min_periods=1).mean() / atr_.replace(0, np.nan))
    mdi = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/n, adjust=False, min_periods=1).mean() / atr_.replace(0, np.nan))
    dx = 100 * ((pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan))
    return dx.ewm(alpha=1/n, adjust=False, min_periods=1).mean().fillna(25.0)


class AttrDict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)
    __setattr__ = dict.__setitem__


def _build_context(df: pd.DataFrame, bar_idx: int, symbol: str):
    """Build a pipeline context from M15 bars.

    *df* must be a M15 DataFrame with a DatetimeIndex (UTC).  H1 context is
    derived by resampling the M15 window on-the-fly — exactly how the live
    loop produces its two-timeframe context.
    """
    from alphaloop.config.assets import get_asset_config
    window = df.iloc[: bar_idx + 1]
    close = window["close"]

    # ── M15 indicators ───────────────────────────────────────────────────────
    ema_fast = float(_ema(close, 21).iloc[-1])
    ema_slow = float(_ema(close, 55).iloc[-1])
    ema200   = float(_ema(close, 200).iloc[-1])
    rsi_val  = float(_rsi(close).iloc[-1])
    atr_val  = float(_atr(window).iloc[-1])
    macd_d   = _macd(close)
    bb_d     = _bb(close)
    adx_val  = float(_adx(window).iloc[-1])

    recent_high = float(window["high"].iloc[-20:].max())
    recent_low  = float(window["low"].iloc[-20:].min())
    bullish_bos = close.iloc[-1] > recent_high * 0.995
    bearish_bos = close.iloc[-1] < recent_low * 1.005

    # ── Swing structure — canonical indicator (same as live market_context) ──
    from alphaloop.data.indicators import find_swing_highs_lows
    _swing_data   = find_swing_highs_lows(window)
    _swing_highs  = _swing_data["swing_highs"]   # list[{"index", "price"}]
    _swing_lows   = _swing_data["swing_lows"]    # list[{"index", "price"}]
    _swing_struct = _swing_data["structure"]     # "bullish" | "bearish" | "ranging"

    vol_s    = window.get("volume", pd.Series(index=window.index, data=1000.0))
    vol_cur  = float(vol_s.iloc[-1] if len(vol_s) else 1000.0)
    vol_ma   = float(vol_s.rolling(20, min_periods=1).mean().iloc[-1] if len(vol_s) else 1000.0)
    price    = float(close.iloc[-1])
    vwap     = float((window["close"] * vol_s).sum() / max(vol_s.sum(), 1e-9))

    # ── H1 indicators — resample M15 window to H1 ────────────────────────────
    # The live loop fetches H1 separately; we replicate it by resampling.
    # RegimeClassifier reads h1["atr_pct"] for volatility; ema200 for HTF trend.
    try:
        h1_window = window.resample("1h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["close"])
    except Exception:
        h1_window = pd.DataFrame()  # fallback: use M15 values below

    if len(h1_window) >= 14:
        h1_atr_val  = float(_atr(h1_window).iloc[-1])
        h1_ema200   = float(_ema(h1_window["close"], min(200, len(h1_window))).iloc[-1])
        h1_ema_fast = float(_ema(h1_window["close"], 21).iloc[-1])
        h1_ema_slow = float(_ema(h1_window["close"], 55).iloc[-1])
    else:
        # Not enough H1 bars yet — fall back to M15 values
        h1_atr_val  = atr_val
        h1_ema200   = ema200
        h1_ema_fast = ema_fast
        h1_ema_slow = ema_slow

    h1_atr_pct = (h1_atr_val / max(price, 1e-9)) * 100.0   # percentage, as live loop

    m15 = {
        "ema200": ema200, "ema_fast": ema_fast, "ema_slow": ema_slow, "alma": ema_fast,
        "atr": max(atr_val, 0.0001), "adx": adx_val, "choppiness": 45.0,
        "macd": float(macd_d["line"].iloc[-1]),
        "macd_signal": float(macd_d["signal"].iloc[-1]),
        "macd_hist": float(macd_d["hist"].iloc[-1]),
        "rsi": rsi_val,
        "bb_upper": float(bb_d["upper"].iloc[-1]),
        "bb_middle": float(bb_d["mid"].iloc[-1]),
        "bb_lower": float(bb_d["lower"].iloc[-1]),
        "bb_pct_b": float(bb_d["pct_b"].iloc[-1]) if not np.isnan(bb_d["pct_b"].iloc[-1]) else 0.5,
        "bb_band_width": float(bb_d["upper"].iloc[-1] - bb_d["lower"].iloc[-1]),
        "volume": vol_cur, "volume_ma": vol_ma, "volume_ratio": vol_cur / max(vol_ma, 1e-9),
        "bos": {
            "bullish_bos": bullish_bos, "bullish_break_atr": 0.5 if bullish_bos else 0.0,
            "bearish_bos": bearish_bos, "bearish_break_atr": 0.5 if bearish_bos else 0.0,
            "swing_high": recent_high, "swing_low": recent_low,
        },
        "fvg": {"bullish": [], "bearish": []},
        "vwap": vwap,
        "swing_structure": _swing_struct,
        "fast_fingers": {
            "is_exhausted_up": rsi_val > 80, "is_exhausted_down": rsi_val < 20,
            "exhaustion_score": max(0, min(100, int(abs(rsi_val - 50) * 2))),
        },
        "tick_jump_atr": 0.2,
        "liq_vacuum": {"bar_range_atr": 0.8, "body_pct": 55},
        "median_spread": 1.5,
        "swing_highs": _swing_highs,
        "swing_lows":  _swing_lows,
    }
    h1 = {
        "atr_pct": h1_atr_pct,   # percentage, not ratio — RegimeClassifier uses this
        "atr": h1_atr_val,
        "ema200": h1_ema200, "ema_fast": h1_ema_fast, "ema_slow": h1_ema_slow,
    }
    m15_bundle = AttrDict({"indicators": m15, "atr": max(atr_val, 0.0001)})
    h1_bundle  = AttrDict({"indicators": h1, "atr_pct": h1_atr_pct})

    ctx = AttrDict({
        "symbol": symbol,
        "trade_direction": "",
        "pip_size": get_asset_config(symbol).pip_size,
        "indicators": {"M15": m15, "H1": h1},
        "timeframes": {"M15": m15_bundle, "H1": h1_bundle},
        "session": AttrDict({"is_weekend": False, "score": 0.75, "name": "london"}),
        "price": AttrDict({
            "bid": price - 0.5, "ask": price + 0.5, "spread": 1.0,
            "time": datetime.now(timezone.utc),
        }),
        "current_price": {"bid": price - 0.5, "ask": price + 0.5, "spread": 1.0},
        "news": [],
        "dxy": {"value": 103.5, "direction": "neutral", "score": 0.5, "change_pct": 0.1,
                "strength": 0.0, "bias": "neutral"},
        "sentiment": {"score": 0.1, "direction": "neutral", "source": "replay",
                      "bias": "neutral"},
        "open_trades": {},
        "tool_results": [],
    })
    ctx["risk_monitor"] = SimpleNamespace(
        kill_switch_active=False, _kill_switch_active=False, _open_risk_usd=0.0,
        account_balance=10000.0,
        can_open_trade=AsyncMock(return_value=(True, "")),
        check_can_open=AsyncMock(return_value=(True, "")),
    )
    ctx["df"] = type("DfShim", (), {"__len__": lambda self: len(window)})()
    return ctx


def _build_orchestrator(symbol: str, strategy: dict, ai_validator=None, timeframe: str = "M15"):
    from alphaloop.pipeline.conviction import ConvictionScorer
    from alphaloop.pipeline.construction import TradeConstructor
    from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
    from alphaloop.pipeline.invalidation import StructuralInvalidator
    from alphaloop.pipeline.market_gate import MarketGate
    from alphaloop.pipeline.orchestrator import PipelineOrchestrator
    from alphaloop.pipeline.quality import StructuralQuality
    from alphaloop.pipeline.regime import RegimeClassifier
    from alphaloop.pipeline.risk_gate import RiskGateRunner
    from alphaloop.pipeline.defaults import load_pipeline_config
    from alphaloop.tools.registry import STAGE_TOOL_MAP, ToolRegistry
    from alphaloop.config.assets import get_asset_config
    from alphaloop.trading.strategy_loader import resolve_construction_params

    registry   = ToolRegistry()
    asset_cfg  = get_asset_config(symbol)
    params     = strategy.get("params", {}) or {}
    enabled    = strategy.get("tools", {}) or {}
    validation = strategy.get("validation") or {}
    cfg        = load_pipeline_config(validation)
    resolved   = resolve_construction_params(strategy, timeframe, asset_cfg)
    cfg["invalidation"]["sl_min_points"] = resolved["sl_min_points"]
    cfg["invalidation"]["sl_max_points"] = resolved["sl_max_points"]
    cfg["invalidation"]["pip_size"]      = asset_cfg.pip_size

    def _stage_tools(stage):
        return [t for n in STAGE_TOOL_MAP.get(stage, [])
                if enabled.get(n, True) and (t := registry.get_tool(n)) is not None]

    tc = TradeConstructor(
        pip_size=asset_cfg.pip_size,
        sl_min_pts=resolved["sl_min_points"],
        sl_max_pts=resolved["sl_max_points"],
        tp1_rr=resolved["tp1_rr"],
        tp2_rr=resolved["tp2_rr"],
        entry_zone_atr_mult=resolved["entry_zone_atr_mult"],
        sl_buffer_atr=resolved["sl_buffer_atr"],
        sl_atr_mult=resolved["sl_atr_mult"],
        tools=_stage_tools("construction"),
    )

    orch = PipelineOrchestrator(
        market_gate=MarketGate(**cfg["market_gate"], tools=_stage_tools("market_gate")),
        regime_classifier=RegimeClassifier(),
        trade_constructor=tc,
        invalidator=StructuralInvalidator(cfg=cfg["invalidation"], tools=_stage_tools("invalidation")),
        quality_scorer=StructuralQuality(tools=_stage_tools("quality")),
        conviction_scorer=ConvictionScorer(
            strategy_params=params or None,
            max_penalty=cfg["conviction"]["max_total_conviction_penalty"],
        ),
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
        hypothesis_tools=_stage_tools("hypothesis"),
        enabled_tools=enabled,
    )
    if ai_validator is not None:
        orch.ai_validator = ai_validator
    return orch


class _StubAIValidator:
    """Stub AI validator for replay — no real LLM call.

    approval_rate: fraction of signals that AI approves (0.0-1.0).
    Uses deterministic approval based on bar index for reproducibility.
    """

    def __init__(self, approval_rate: float):
        self._rate = approval_rate
        self._call_count = 0

    async def validate(self, signal, regime, quality, conviction, context, *, mode="algo_ai"):
        self._call_count += 1
        approved = (self._call_count % 100) < int(self._rate * 100)

        if approved:
            return signal  # pass-through

        # Rejected
        if mode == "ai_signal":
            # Gate-2: soft veto — return signal with reduced confidence
            from alphaloop.pipeline.types import CandidateSignal
            soft_conf = round(max(0.30, signal.raw_confidence - 0.15), 4)
            return CandidateSignal(
                direction=signal.direction,
                setup_type=signal.setup_type,
                entry_zone=signal.entry_zone,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                raw_confidence=soft_conf,
                rr_ratio=signal.rr_ratio,
                signal_sources=signal.signal_sources,
                reasoning=signal.reasoning,
            )
        else:
            # algo_ai: hard block
            return None


async def _run_mode(
    df: pd.DataFrame,
    start_index: int,
    symbol: str,
    strategy: dict,
    mode: str,
    ai_approval_rate: float,
    timeframe: str = "M15",
) -> dict[str, Any]:
    from alphaloop.pipeline.types import DirectionHypothesis, CycleOutcome

    # Inject AI validator for algo_ai and ai_signal modes
    ai_validator = None
    if mode in ("algo_ai", "ai_signal"):
        ai_validator = _StubAIValidator(ai_approval_rate)

    orchestrator = _build_orchestrator(symbol, strategy, ai_validator, timeframe=timeframe)

    async def signal_gen(ctx, regime):
        h1 = ctx["indicators"].get("H1", {})
        m15 = ctx["indicators"].get("M15", {})
        price = ctx["price"].ask if hasattr(ctx["price"], "ask") else ctx["price"]["ask"]
        ema200 = float(h1.get("ema200", 0) or 0) or float(m15.get("ema200", 0) or 0)
        direction = "BUY" if price >= ema200 else "SELL"
        return DirectionHypothesis(
            direction=direction, confidence=0.65, setup_tag="pullback",
            reasoning="replay stub — aligned with HTF trend",
            source_names="replay_stub",
        )

    stats = {
        "bars": 0,
        # --- Funnel stages (every bar goes into exactly one bucket) ---
        "no_signal": 0,              # Stage 3: signal gen returned nothing
        "no_construction": 0,        # Stage 3B: hypothesis ok, no valid SL/TP built
        "rejected_market_gate": 0,   # Stage 1: market not physically tradeable
        "rejected_invalidation": 0,  # Stage 4A: HARD_INVALIDATE
        "rejected_ai_block": 0,      # Stage 6: AI validator hard block
        "rejected_risk": 0,          # Stage 7: risk gate / exec guard block
        "held_regime": 0,            # Stage 3: setup type not in allowed_setups
        "held_quality_floor": 0,     # Stage 4B: quality overall < floor
        "held_conviction_low": 0,    # Stage 5: conviction < effective_min
        "executed": 0,
        # --- Derived metrics ---
        "ai_soft_veto_applied": 0,
        "conv_scores": [],
        "quality_scores": [],
        "quality_floor_triggered": 0,
        # Penalty waterfall tracking
        "invalidation_penalty_total": 0.0,
        "conflict_penalty_total": 0.0,
        "portfolio_penalty_total": 0.0,
        "soft_invalidate_count": 0,
        "soft_check_fires": {},
        "raw_conv_scores": [],
    }

    for bar_idx in range(start_index, len(df)):
        ctx = _build_context(df, bar_idx, symbol)
        result = await orchestrator.run(ctx, signal_gen, symbol=symbol, mode=mode)
        stats["bars"] += 1

        outcome = result.outcome

        if outcome == CycleOutcome.NO_SIGNAL:
            stats["no_signal"] += 1

        elif outcome == CycleOutcome.NO_CONSTRUCTION:
            # Direction hypothesis generated but SL/TP could not be derived
            stats["no_construction"] += 1

        elif outcome == CycleOutcome.REJECTED:
            if result.market_gate is not None and not result.market_gate.tradeable:
                stats["rejected_market_gate"] += 1
            elif result.invalidation is not None and result.invalidation.severity == "HARD_INVALIDATE":
                stats["rejected_invalidation"] += 1
            else:
                # AI hard block, risk gate, exec guard, or pipeline error
                stats["rejected_ai_block"] += 1

        elif outcome == CycleOutcome.HELD:
            if result.conviction is not None:
                if result.conviction.quality_floor_triggered:
                    stats["held_quality_floor"] += 1
                else:
                    stats["held_conviction_low"] += 1
            else:
                # HELD with no conviction = regime filtered setup type out
                stats["held_regime"] += 1

        elif outcome == CycleOutcome.TRADE_OPENED:
            stats["executed"] += 1

        # DELAYED / ORDER_FAILED — rare, fall into no bucket but bars still counted

        if result.quality is not None:
            stats["quality_scores"].append(result.quality.overall_score)

        if result.conviction is not None:
            cs = result.conviction.score
            stats["conv_scores"].append(cs)
            if result.conviction.quality_floor_triggered:
                stats["quality_floor_triggered"] += 1

            # Penalty breakdown
            inv_pen = getattr(result.conviction, "invalidation_penalty", 0.0) or 0.0
            con_pen = getattr(result.conviction, "conflict_penalty", 0.0) or 0.0
            por_pen = getattr(result.conviction, "portfolio_penalty", 0.0) or 0.0
            stats["invalidation_penalty_total"] += inv_pen
            stats["conflict_penalty_total"] += con_pen
            stats["portfolio_penalty_total"] += por_pen
            if inv_pen > 0:
                stats["soft_invalidate_count"] += 1

            # Reconstruct raw_conviction (before penalties) from quality group scores
            if result.quality and result.quality.group_scores:
                stats["raw_conv_scores"].append(result.quality.overall_score)

        # Track which soft checks fired
        if result.invalidation is not None:
            for f in result.invalidation.failures:
                if f.severity == "SOFT_INVALIDATE":
                    k = f.check_name
                    stats["soft_check_fires"][k] = stats["soft_check_fires"].get(k, 0) + 1

    return stats


def _summarise(stats: dict) -> dict:
    cs  = stats["conv_scores"]
    qs  = stats["quality_scores"]
    rcs = stats["raw_conv_scores"]
    n   = stats["bars"]
    n_conv = max(len(cs), 1)
    accounted = (
        stats["no_signal"] + stats["no_construction"] +
        stats["rejected_market_gate"] + stats["rejected_invalidation"] +
        stats["rejected_ai_block"] + stats["rejected_risk"] +
        stats["held_regime"] + stats["held_quality_floor"] +
        stats["held_conviction_low"] + stats["executed"]
    )
    return {
        "bars":                   n,
        "unaccounted":            n - accounted,  # should always be 0 — sentinel
        # Funnel breakdown
        "no_signal":              stats["no_signal"],
        "no_construction":        stats["no_construction"],
        "rejected_market_gate":   stats["rejected_market_gate"],
        "rejected_invalidation":  stats["rejected_invalidation"],
        "rejected_ai_block":      stats["rejected_ai_block"],
        "held_regime":            stats["held_regime"],
        "held_quality_floor":     stats["held_quality_floor"],
        "held_conviction_low":    stats["held_conviction_low"],
        "executed":               stats["executed"],
        "exec_rate_pct":          round(stats["executed"] / max(n, 1) * 100, 1),
        "quality_floor_rate_pct": round(stats["quality_floor_triggered"] / max(n, 1) * 100, 1),
        # Conviction stats (bars that reached Stage 5)
        "conv_n":    len(cs),
        "conv_min":  round(min(cs), 2) if cs else None,
        "conv_mean": round(sum(cs) / len(cs), 2) if cs else None,
        "conv_max":  round(max(cs), 2) if cs else None,
        "conv_p50":  round(float(np.percentile(cs, 50)), 2) if cs else None,
        "conv_p75":  round(float(np.percentile(cs, 75)), 2) if cs else None,
        "conv_above60": sum(1 for s in cs if s >= 60),
        "conv_above70": sum(1 for s in cs if s >= 70),
        "qual_mean": round(sum(qs) / len(qs), 2) if qs else None,
        "qual_max":  round(max(qs), 2) if qs else None,
        # Penalty waterfall
        "inv_pen_mean":          round(stats["invalidation_penalty_total"] / n_conv, 2),
        "conf_pen_mean":         round(stats["conflict_penalty_total"] / n_conv, 2),
        "port_pen_mean":         round(stats["portfolio_penalty_total"] / n_conv, 2),
        "total_pen_mean":        round((stats["invalidation_penalty_total"] +
                                        stats["conflict_penalty_total"] +
                                        stats["portfolio_penalty_total"]) / n_conv, 2),
        "soft_invalidate_pct":   round(stats["soft_invalidate_count"] / max(n, 1) * 100, 1),
        "soft_check_fires":      dict(sorted(stats["soft_check_fires"].items(),
                                             key=lambda x: -x[1])),
    }


def _print_table(results: dict[str, dict], gate1: dict, timeframe: str = "M15"):
    w = 90
    modes = list(results.keys())
    first = modes[0]
    actual_bars = results[first]["bars"]

    print("\n" + "=" * w)
    print(f"  Gate-4 Funnel — Full Stage Breakdown  (BTCUSD {timeframe}, {actual_bars} bars, yfinance)")
    print("=" * w)

    col_w = 16
    hdr = f"  {'Metric':<32}"
    for m in ["Gate-1 (base)"] + modes:
        hdr += f"  {m:>{col_w}}"
    print(hdr)
    print("  " + "-" * (w - 2))

    def row(label, *vals, sep=False):
        r = f"  {label:<32}"
        for v in vals:
            r += f"  {str(v):>{col_w}}"
        print(r)
        if sep:
            print("  " + "·" * (w - 2))

    g1 = gate1

    row("Stage 1 — market gate block",
        g1.get("rejected_market_gate", "152"),
        *[results[m]["rejected_market_gate"] for m in modes])

    row("Stage 3 — no signal",
        g1.get("no_signal", "n/a"),
        *[results[m]["no_signal"] for m in modes])

    row("Stage 3B — no construction",
        g1.get("no_construction", "n/a"),
        *[results[m]["no_construction"] for m in modes])

    row("Stage 3B — regime filter",
        g1.get("held_regime", "n/a"),
        *[results[m]["held_regime"] for m in modes])

    row("Stage 4A — hard invalidation",
        g1.get("rejected_invalidation", "n/a"),
        *[results[m]["rejected_invalidation"] for m in modes])

    row("Stage 4B — quality floor",
        g1.get("held_quality_floor", "89 (100%)"),
        *[f"{results[m]['held_quality_floor']} ({results[m]['quality_floor_rate_pct']}%)"
          for m in modes])

    row("Stage 5 — conviction < min",
        g1.get("held_conviction_low", "0"),
        *[results[m]["held_conviction_low"] for m in modes])

    row("Stage 6 — AI hard block",
        g1.get("rejected_ai_block", "0"),
        *[results[m]["rejected_ai_block"] for m in modes])

    row(">>> EXECUTED",
        g1.get("executed", "0"),
        *[f"{results[m]['executed']} ({results[m]['exec_rate_pct']}%)"
          for m in modes], sep=True)

    unaccounted = [results[m]["unaccounted"] for m in modes]
    if any(v != 0 for v in unaccounted):
        row("*** UNACCOUNTED (bug) ***",
            "n/a",
            *unaccounted)
        print("  " + "." * (w - 2))

    print("  " + "·" * (w - 2))

    row("Conv score min",
        g1.get("conv_min", "n/a"),
        *[results[m]["conv_min"] for m in modes])

    row("Conv score mean",
        g1.get("conv_mean", "34.03"),
        *[results[m]["conv_mean"] for m in modes])

    row("Conv score p50 (median)",
        g1.get("conv_p50", "n/a"),
        *[results[m]["conv_p50"] for m in modes])

    row("Conv score p75",
        g1.get("conv_p75", "n/a"),
        *[results[m]["conv_p75"] for m in modes])

    row("Conv score max",
        g1.get("conv_max", "53.1"),
        *[results[m]["conv_max"] for m in modes])

    row("Conv >= 60",
        g1.get("conv_above60", "0"),
        *[results[m]["conv_above60"] for m in modes])

    row("Conv >= 70 (strong entry)",
        g1.get("conv_above70", "0"),
        *[results[m]["conv_above70"] for m in modes])

    row("Quality score mean",
        g1.get("qual_mean", "~34"),
        *[results[m]["qual_mean"] for m in modes])

    row("Quality score max",
        g1.get("qual_max", "~53"),
        *[results[m]["qual_max"] for m in modes])

    print("  " + "·" * (w - 2))
    print("  PENALTY WATERFALL (avg per bar reaching Stage 5):")

    row("  Invalidation penalty (avg)",
        "unknown",
        *[results[m]["inv_pen_mean"] for m in modes])

    row("  Conflict penalty (avg)",
        "unknown",
        *[results[m]["conf_pen_mean"] for m in modes])

    row("  Portfolio penalty (avg)",
        "unknown",
        *[results[m]["port_pen_mean"] for m in modes])

    row("  Total penalty (avg)",
        "unknown",
        *[results[m]["total_pen_mean"] for m in modes])

    row("  Bars with soft penalty > 0",
        "unknown",
        *[f"{results[m]['soft_invalidate_pct']}%" for m in modes])

    # Show which checks fire — just for algo_only (same for all)
    first_mode = modes[0]
    fires = results[first_mode].get("soft_check_fires", {})
    if fires:
        print()
        print("  Soft check fire counts (algo_only):")
        for check, count in fires.items():
            print(f"    {check:<30} {count:>5} bars")

    print("=" * w)
    print()
    print("  Notes:")
    print("  - Primary timeframe: M15 (strategy signal timeframe). H1 derived by resampling.")
    print("  - Gate-1 baseline was H1 — not comparable; shown as n/a.")
    print("  - Gate-2: 10 tools recalibrated to 0-100 scoring contract")
    print("  - Gate-3: regime ATR% unit fix, volume_filter zero-vol, vwap/liq 20->10pt")
    print("  - Gate-4: swing_alignment pullback HARD->SOFT, 20pt->8pt penalty")
    print("    (opposing short-term swing IS the pullback retracement — expected, not a fault)")
    print("  - algo_ai: AI hard-block at Stage 6 (approval_rate controls stub)")
    print("  - ai_signal: AI soft-veto (-0.15 conf) instead of hard block")
    print("  - AI stub: deterministic, no real LLM call, for structural measurement only")
    print()


async def _main(symbol: str, bars: int, timeframe: str, ai_rate: float):
    from alphaloop.data.fetcher import OHLCVFetcher

    strategy_path = Path(f"strategy_versions/phantom-knight-{symbol}_ai_v1.json")
    if not strategy_path.exists():
        for p in Path("strategy_versions").glob(f"*{symbol}*.json"):
            strategy_path = p
            break
    if not strategy_path.exists():
        raise SystemExit(f"No strategy file for {symbol}")
    strategy = json.loads(strategy_path.read_text())
    logger.info("Loaded strategy: %s (%s)", strategy_path.name, timeframe)

    fetcher = OHLCVFetcher(symbol=symbol, use_mt5=False)
    # Warmup = 200 bars for EMA200; extra 50 so H1 resample has enough H1 bars early on
    df = await asyncio.to_thread(fetcher._fetch_yfinance_sync, timeframe, bars + 250)
    if not df.empty and "time" in df.columns:
        df = df.copy()
        import pandas as pd
        df.index = pd.to_datetime(df["time"], utc=True)
    if len(df) < 250:
        raise SystemExit(f"Not enough bars: {len(df)}")

    start_index = max(250, len(df) - bars)
    actual_bars = len(df) - start_index
    logger.info("Replaying %d bars (%s %s) across 3 modes...", actual_bars, symbol, timeframe)

    results = {}
    for mode in ("algo_only", "algo_ai", "ai_signal"):
        logger.info("Running mode: %s (AI approval rate: %.0f%%)", mode, ai_rate * 100)
        raw = await _run_mode(df, start_index, symbol, strategy, mode, ai_rate, timeframe=timeframe)
        results[mode] = _summarise(raw)
        logger.info(
            "  %s: executed=%d qfloor=%d conv_low=%d no_constr=%d regime=%d",
            mode, raw["executed"], raw["held_quality_floor"],
            raw["held_conviction_low"], raw["no_construction"], raw["held_regime"],
        )

    # Gate-1 baseline was measured on H1 bars — not comparable to M15 runs.
    # Shown as reference only; all numbers marked n/a for M15 replays.
    gate1 = {
        "bars": "n/a (H1)",
        "rejected_market_gate": "n/a",
        "no_signal": "n/a",
        "no_construction": "n/a",
        "rejected_invalidation": "n/a",
        "held_regime": "n/a",
        "held_quality_floor": "n/a",
        "held_conviction_low": "n/a",
        "rejected_ai_block": "n/a",
        "executed": "n/a",
        "conv_min": "n/a",
        "conv_mean": "n/a",
        "conv_p50": "n/a",
        "conv_p75": "n/a",
        "conv_max": "n/a",
        "conv_above60": "n/a",
        "conv_above70": "n/a",
        "qual_mean": "n/a",
        "qual_max": "n/a",
    }

    _print_table(results, gate1, timeframe=timeframe)

    print("  Raw JSON results:")
    print(json.dumps({"gate1_baseline": gate1, "gate2_results": results}, indent=2, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--bars", type=int, default=960,
                        help="Number of M15 bars to replay (960 ≈ 10 trading days, same window as 241 H1)")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--ai-approval-rate", type=float, default=0.6,
                        help="Fraction of signals AI approves (0.0=reject all, 1.0=approve all)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    asyncio.run(_main(args.symbol, args.bars, args.timeframe, args.ai_approval_rate))


if __name__ == "__main__":
    main()
