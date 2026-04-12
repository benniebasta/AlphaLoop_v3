"""Gate-2 measurement — run the REAL ``PipelineOrchestrator`` over recent
historical bars (via yfinance) and write per-stage rows to
``pipeline_stage_decisions`` with ``source='historical_replay'``.

This is the closest thing to a live cycle we can produce without a broker:
- Real OHLCV bars pulled through the existing ``OHLCVFetcher`` (yfinance mode).
- Real indicators computed on each window.
- Real ``PipelineOrchestrator`` + real ``StructuralQuality`` + real
  ``ConvictionScorer`` with the post-Gate-2 ``STAGE_TOOL_MAP``.
- Real ``TradeDecision`` projection written to the Gate-1 ledger.

The observability UI at ``#observability`` with ``source=historical_replay``
will then show exactly what Gate-2 is doing on genuine market data.

Usage::

    python -m scripts.replay_live_cycles --symbol BTCUSD --bars 100 --timeframe H1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pandas as pd

from alphaloop.data.fetcher import OHLCVFetcher
from alphaloop.pipeline.orchestrator import PipelineOrchestrator
from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.construction import TradeConstructor
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.quality import StructuralQuality
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.risk_gate import RiskGateRunner
from alphaloop.pipeline.types import build_trade_decision
from alphaloop.signals.algorithmic import AlgorithmicSignalEngine
from alphaloop.tools.registry import STAGE_TOOL_MAP, ToolRegistry
from alphaloop.config.assets import get_asset_config

logger = logging.getLogger("replay_live_cycles")


# ═══════════════════════════════════════════════════════════════════════════
# Indicator helpers — small, self-contained, no pandas-ta dependency
# ═══════════════════════════════════════════════════════════════════════════


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period, min_periods=1).mean()
    loss = -delta.where(delta < 0, 0.0).rolling(period, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def _macd(close: pd.Series) -> dict[str, pd.Series]:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    line = ema12 - ema26
    signal = _ema(line, 9)
    hist = line - signal
    return {"line": line, "signal": signal, "hist": hist}


def _bb(close: pd.Series, period: int = 20, stdevs: float = 2.0) -> dict[str, pd.Series]:
    mid = close.rolling(period, min_periods=1).mean()
    std = close.rolling(period, min_periods=1).std().fillna(0)
    upper = mid + stdevs * std
    lower = mid - stdevs * std
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan)
    return {"upper": upper, "mid": mid, "lower": lower, "pct_b": pct_b}


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=1).mean() / atr_.replace(0, np.nan))
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=1).mean() / atr_.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=1).mean().fillna(25.0)


def _choppiness(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].rolling(period, min_periods=1).max()
    low = df["low"].rolling(period, min_periods=1).min()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    sum_tr = tr.rolling(period, min_periods=1).sum()
    ratio = sum_tr / (high - low).replace(0, np.nan)
    return (100 * np.log10(ratio) / np.log10(period)).fillna(50.0)


# ═══════════════════════════════════════════════════════════════════════════
# Per-bar MarketContext construction
# ═══════════════════════════════════════════════════════════════════════════


class AttrDict(dict):
    """Dict with attribute access — same shape as the live loop's context."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    __setattr__ = dict.__setitem__


def _build_context(df: pd.DataFrame, bar_index: int, symbol: str):
    """Build a MarketContext-shaped object for a single bar, using all bars
    up to and including ``bar_index``.

    Mirrors ``trading/loop.py::_build_context``: returns an ``AttrDict`` so
    both ``ctx.foo`` and ``ctx.get("foo")`` work, matching what the live
    algorithmic signal engine and pipeline tools expect.
    """
    window = df.iloc[: bar_index + 1]
    close = window["close"]

    ema_fast = float(_ema(close, 21).iloc[-1])
    ema_slow = float(_ema(close, 55).iloc[-1])
    ema200 = float(_ema(close, 200).iloc[-1])
    rsi_val = float(_rsi(close).iloc[-1])
    atr_val = float(_atr(window).iloc[-1])
    macd_d = _macd(close)
    bb_d = _bb(close)
    adx_val = float(_adx(window).iloc[-1])
    chop_val = float(_choppiness(window).iloc[-1])

    recent_high = float(window["high"].iloc[-20:].max())
    recent_low = float(window["low"].iloc[-20:].min())
    bullish_bos = close.iloc[-1] > recent_high * 0.995
    bearish_bos = close.iloc[-1] < recent_low * 1.005

    volume_s = window.get("volume", pd.Series(index=window.index, data=1000.0))
    vol_current = float(volume_s.iloc[-1] if len(volume_s) else 1000.0)
    vol_ma = float(volume_s.rolling(20, min_periods=1).mean().iloc[-1] if len(volume_s) else 1000.0)

    price_close = float(close.iloc[-1])
    vwap_val = float((window["close"] * volume_s).sum() / max(volume_s.sum(), 1e-9))

    indicators_m15 = {
        "ema200": ema200,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "alma": ema_fast,
        "atr": max(atr_val, 0.0001),
        "adx": adx_val,
        # Live loop stores choppiness as a float; RegimeClassifier expects it
        # (choppiness_index tool's extract_features expects a dict, but that
        # is a separate bug — it will crash silently here, same as production).
        "choppiness": chop_val,
        "macd": float(macd_d["line"].iloc[-1]),
        "macd_signal": float(macd_d["signal"].iloc[-1]),
        "macd_hist": float(macd_d["hist"].iloc[-1]),
        "rsi": rsi_val,
        "bb_upper": float(bb_d["upper"].iloc[-1]),
        "bb_middle": float(bb_d["mid"].iloc[-1]),
        "bb_lower": float(bb_d["lower"].iloc[-1]),
        "bb_pct_b": float(bb_d["pct_b"].iloc[-1]) if not np.isnan(bb_d["pct_b"].iloc[-1]) else 0.5,
        "bollinger_position": float(bb_d["pct_b"].iloc[-1]) if not np.isnan(bb_d["pct_b"].iloc[-1]) else 0.5,
        "volume": vol_current,
        "volume_ma": vol_ma,
        "volume_ratio": vol_current / max(vol_ma, 1e-9),
        "bos": {
            "bullish_bos": bullish_bos,
            "bullish_break_atr": 0.5 if bullish_bos else 0.0,
            "bearish_bos": bearish_bos,
            "bearish_break_atr": 0.5 if bearish_bos else 0.0,
            "swing_high": recent_high,
            "swing_low": recent_low,
        },
        "fvg": {"bullish": [], "bearish": []},
        "vwap": vwap_val,
        "swing_structure": "bullish" if ema_fast > ema_slow else "bearish",
        "fast_fingers": {
            "is_exhausted_up": rsi_val > 80,
            "is_exhausted_down": rsi_val < 20,
            "exhaustion_score": max(0, min(100, int(abs(rsi_val - 50) * 2))),
        },
        "tick_jump_atr": 0.2,
        "liq_vacuum": {"bar_range_atr": 0.8, "body_pct": 55},
        "median_spread": 1.5,
    }

    h1_atr_pct = (atr_val / max(price_close, 1e-9)) * 100.0
    h1_indicators = {
        "atr_pct": h1_atr_pct,
        "atr": atr_val,
        "ema200": ema200,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
    }
    m15_bundle = AttrDict({"indicators": indicators_m15, "atr": max(atr_val, 0.0001)})
    h1_bundle = AttrDict({"indicators": h1_indicators, "atr_pct": h1_atr_pct})

    ctx = AttrDict({
        "symbol": symbol,
        "trade_direction": "",
        "pip_size": get_asset_config(symbol).pip_size,
        "indicators": {"M15": indicators_m15, "H1": h1_indicators},
        "timeframes": {"M15": m15_bundle, "H1": h1_bundle},
        "session": AttrDict({"is_weekend": False, "score": 0.75, "name": "london"}),
        "price": AttrDict({
            "bid": price_close - 0.5,
            "ask": price_close + 0.5,
            "spread": 1.0,
            # Use "now" so MarketGate's stale-feed check passes.
            "time": datetime.now(timezone.utc),
        }),
        "current_price": {"bid": price_close - 0.5, "ask": price_close + 0.5, "spread": 1.0},
        "news": [],
        "dxy": {"value": 103.5, "direction": "neutral", "score": 0.5, "change_pct": 0.1},
        "sentiment": {"score": 0.1, "direction": "neutral", "source": "polymarket"},
        "open_trades": {},
        "tool_results": [],
    })
    ctx["risk_monitor"] = SimpleNamespace(
        kill_switch_active=False,
        _kill_switch_active=False,
        _open_risk_usd=0.0,
        account_balance=10000.0,
        can_open_trade=AsyncMock(return_value=(True, "")),
        check_can_open=AsyncMock(return_value=(True, "")),
    )
    ctx["df"] = type("DfShim", (), {"__len__": lambda self: len(window)})()
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Orchestrator wiring — same tools the live loop gets
# ═══════════════════════════════════════════════════════════════════════════


def _get_stage_tools(stage: str, registry: ToolRegistry, enabled: dict[str, bool]) -> list:
    names = STAGE_TOOL_MAP.get(stage, [])
    tools: list = []
    for name in names:
        if not enabled.get(name, True):
            continue
        tool = registry.get_tool(name)
        if tool is not None:
            tools.append(tool)
    return tools


def _build_orchestrator(
    symbol: str,
    strategy_params: dict[str, Any],
    enabled_tools: dict[str, bool],
    strategy_validation: dict[str, Any] | None = None,
) -> PipelineOrchestrator:
    """Mirror the live loop's ``_build_v4_orchestrator``: load the pipeline
    config, override SL bounds with the per-asset values, then build every
    stage with the same tool wiring."""
    from alphaloop.pipeline.defaults import load_pipeline_config

    registry = ToolRegistry()
    asset_cfg = get_asset_config(symbol)
    cfg = load_pipeline_config(strategy_validation or {})
    cfg["invalidation"]["sl_min_points"] = asset_cfg.sl_min_points
    cfg["invalidation"]["sl_max_points"] = asset_cfg.sl_max_points
    cfg["invalidation"]["pip_size"] = asset_cfg.pip_size

    trade_constructor = TradeConstructor(
        pip_size=asset_cfg.pip_size,
        sl_min_pts=asset_cfg.sl_min_points,
        sl_max_pts=asset_cfg.sl_max_points,
        tp1_rr=float(strategy_params.get("tp1_rr", asset_cfg.tp1_rr)),
        tp2_rr=float(strategy_params.get("tp2_rr", asset_cfg.tp2_rr)),
        entry_zone_atr_mult=float(strategy_params.get("entry_zone_atr_mult", asset_cfg.entry_zone_atr_mult)),
        sl_buffer_atr=float(strategy_params.get("sl_buffer_atr", 0.15)),
        sl_atr_mult=float(strategy_params.get("sl_atr_mult", asset_cfg.sl_atr_mult)),
        tools=_get_stage_tools("construction", registry, enabled_tools),
    )

    return PipelineOrchestrator(
        market_gate=MarketGate(
            **cfg["market_gate"],
            tools=_get_stage_tools("market_gate", registry, enabled_tools),
        ),
        regime_classifier=RegimeClassifier(),
        trade_constructor=trade_constructor,
        invalidator=StructuralInvalidator(
            cfg=cfg["invalidation"],
            tools=_get_stage_tools("invalidation", registry, enabled_tools),
        ),
        quality_scorer=StructuralQuality(
            tools=_get_stage_tools("quality", registry, enabled_tools),
        ),
        conviction_scorer=ConvictionScorer(
            strategy_params=strategy_params or None,
            max_penalty=cfg["conviction"]["max_total_conviction_penalty"],
        ),
        risk_gate=RiskGateRunner(),
        execution_guard=ExecutionGuardRunner(),
        hypothesis_tools=_get_stage_tools("hypothesis", registry, enabled_tools),
        enabled_tools=enabled_tools,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════


async def _open_session_factory():
    from alphaloop.core.config import AppConfig
    from alphaloop.db.engine import create_db_engine
    from alphaloop.db.session import create_session_factory

    config = AppConfig()
    engine = create_db_engine(config.db)
    return create_session_factory(engine), engine


async def _write_stage_rows(
    session_factory,
    cycle_id: str,
    decision,
    journey,
    *,
    symbol: str,
    mode: str,
) -> None:
    from alphaloop.db.models.pipeline import PipelineStageDecision

    async with session_factory() as session:
        for idx, stage in enumerate(journey.stages):
            session.add(
                PipelineStageDecision(
                    occurred_at=decision.occurred_at,
                    cycle_id=cycle_id,
                    source="historical_replay",
                    symbol=symbol,
                    instance_id="replay",
                    mode=mode,
                    stage=stage.stage,
                    stage_index=idx,
                    status=stage.status,
                    blocked_by=stage.blocked_by,
                    detail=(stage.detail or "")[:2000] or None,
                    payload=stage.payload or None,
                    outcome=decision.outcome,
                    reject_stage=decision.reject_stage,
                    direction=decision.direction,
                    setup_type=decision.setup_type,
                    conviction_score=decision.conviction_score,
                    size_multiplier=decision.size_multiplier,
                    latency_ms=decision.latency_ms,
                )
            )
        await session.commit()


async def _run(symbol: str, bars: int, timeframe: str, mode: str) -> dict:
    # Load active strategy
    strategy_path = Path(f"strategy_versions/phantom-knight-{symbol}_ai_v1.json")
    if not strategy_path.exists():
        # Fall back to any strategy for this symbol
        for p in Path("strategy_versions").glob(f"*{symbol}*.json"):
            strategy_path = p
            break
    if not strategy_path.exists():
        raise SystemExit(f"No strategy file found for {symbol}")
    strategy = json.loads(strategy_path.read_text())
    params = strategy.get("params", {}) or {}
    enabled_tools = strategy.get("tools", {}) or {}
    logger.info("Loaded strategy %s (enabled tools: %d/%d)",
                strategy_path.name,
                sum(1 for v in enabled_tools.values() if v),
                len(enabled_tools))

    # Fetch bars via yfinance (no MT5 required). We bypass ``get_ohlcv`` which
    # has a tz-aware Timestamp bug at data/fetcher.py:131 on recent pandas
    # — the sync yfinance fetch path itself works fine.
    fetcher = OHLCVFetcher(symbol=symbol, use_mt5=False)
    df = await asyncio.to_thread(
        fetcher._fetch_yfinance_sync, timeframe, bars + 100
    )
    if not df.empty and "time" in df.columns:
        df = df.copy()
        df.index = pd.to_datetime(df["time"], utc=True)
    logger.info("Fetched %d bars of %s %s via yfinance", len(df), symbol, timeframe)
    if len(df) < 100:
        raise SystemExit(f"Not enough bars: {len(df)} (need 100+ for indicator warmup)")

    # Build orchestrator once; it can be reused per bar
    strategy_validation = strategy.get("validation") or {}
    orchestrator = _build_orchestrator(symbol, params, enabled_tools, strategy_validation)

    # Stub signal generator — picks direction aligned with the HTF trend
    # (price vs H1 EMA200) so the structure tools at Stage 4B can produce
    # positive scores for signals that go WITH the market, not against it.
    # In production this is done by the LLM signal engine; this stub is a
    # cheap reasonable substitute that lets us measure Stages 4A-8 on real
    # market data without a broker or API keys.
    from alphaloop.pipeline.types import DirectionHypothesis

    async def signal_generator(ctx, regime):
        h1_ind = ctx["indicators"]["H1"] if "H1" in ctx["indicators"] else {}
        m15_ind = ctx["indicators"]["M15"] if "M15" in ctx["indicators"] else {}
        price = ctx["price"].ask if hasattr(ctx["price"], "ask") else ctx["price"]["ask"]
        ema200 = float(h1_ind.get("ema200", 0) or 0) or float(m15_ind.get("ema200", 0) or 0)
        direction = "BUY" if price >= ema200 else "SELL"
        return DirectionHypothesis(
            direction=direction,
            confidence=0.65,
            setup_tag="pullback",
            reasoning="replay stub — aligned with HTF trend (price vs H1 EMA200)",
            source_names="replay_stub",
        )

    session_factory, engine = await _open_session_factory()
    try:
        # Live pipeline requires 200 bars of history at every cycle — ensure
        # every bar we replay has at least that much lookback, even if the
        # yfinance response is shorter than we'd like. Clamp bars accordingly.
        start_index = max(200, len(df) - bars)
        if start_index >= len(df):
            raise SystemExit(
                f"Not enough bars after 200-bar warmup: only {len(df)} bars "
                f"fetched, start_index={start_index}. Try a smaller --bars or "
                f"a coarser --timeframe (D1 has more yfinance history)."
            )
        executed = 0
        reached_risk_gate = 0
        reached_conviction = 0
        held_at_conviction = 0
        rejected_at_invalidation = 0
        no_signal = 0
        other = 0
        conviction_scores: list[float] = []

        for bar_idx in range(start_index, len(df)):
            ctx = _build_context(df, bar_idx, symbol)

            result = await orchestrator.run(
                ctx, signal_generator, symbol=symbol, mode=mode,
            )
            decision = build_trade_decision(result, symbol=symbol, mode=mode)
            cycle_id = f"replay-{symbol}-{bar_idx}-{int(decision.occurred_at.timestamp() * 1000)}"

            await _write_stage_rows(
                session_factory, cycle_id, decision, result.journey,
                symbol=symbol, mode=mode,
            )

            outcome = decision.outcome
            if outcome == "trade_opened":
                executed += 1
            elif outcome == "no_signal":
                no_signal += 1

            if result.conviction is not None:
                reached_conviction += 1
                if result.conviction.score is not None:
                    conviction_scores.append(float(result.conviction.score))
                if result.conviction.decision == "HOLD":
                    held_at_conviction += 1
            if result.risk_gate is not None:
                reached_risk_gate += 1
            if result.invalidation is not None and result.invalidation.severity == "HARD_INVALIDATE":
                rejected_at_invalidation += 1

        total = len(df) - start_index
        cs = conviction_scores
        conv_stats = {
            "n": len(cs),
            "min": round(min(cs), 2) if cs else None,
            "mean": round(sum(cs) / len(cs), 2) if cs else None,
            "max": round(max(cs), 2) if cs else None,
            "above_70": sum(1 for s in cs if s >= 70),
            "above_60": sum(1 for s in cs if s >= 60),
            "above_50": sum(1 for s in cs if s >= 50),
        }
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "bars_replayed": total,
            "executed": executed,
            "reached_conviction": reached_conviction,
            "held_at_conviction": held_at_conviction,
            "reached_risk_gate": reached_risk_gate,
            "rejected_at_invalidation": rejected_at_invalidation,
            "no_signal": no_signal,
            "conviction_score_stats": conv_stats,
        }
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--bars", type=int, default=100, help="Number of bars to replay (after 200-bar warmup)")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--mode", default="algo_only", choices=["algo_only", "algo_ai", "ai_signal"])
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    outcome = asyncio.run(_run(args.symbol, args.bars, args.timeframe, args.mode))
    print(json.dumps(outcome, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
