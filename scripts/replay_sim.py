"""
Replay simulation — 3 signal modes × pipeline funnel.

Fetches 30 days of BTC-USD hourly data via yfinance, then runs every bar
through the same staged filter logic that make_signal_fn() uses in the
backtester (runner.py). Stage counts are collected per mode and printed
as ASCII tables.

No DB, no server, no event stream. Pure data → signal → table output.

Run with:
    python scripts/replay_sim.py [--symbol BTC-USD] [--days 30]
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Force UTF-8 output on Windows so box-drawing chars render correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import yfinance as yf

# ── Reuse exact helpers from the backtester ───────────────────────────────────
from alphaloop.backtester.runner import (
    _adx_arrays,
    _bollinger_pct_b,
    _detect_bos_simple,
    _ema,
    _ema_from_array,
    _has_fvg,
    _rolling_swing_hi_lo,
    _rsi,
    _swing_structure_simple,
)
from alphaloop.signals.conditions import (
    check_ema_crossover,
    check_rsi_reversal,
    combine,
)
from alphaloop.utils.time import get_session_score_for_hour

# ── Strategy params (phantom-knight-BTCUSD_ai_v1.json) ───────────────────────
EMA_FAST = 21
EMA_SLOW = 55
RSI_PERIOD = 14
RSI_OB = 70.0
RSI_OS = 30.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL_P = 9
BB_PERIOD = 20
BB_STD = 2.0
ADX_PERIOD = 14
ADX_MIN = 20.0
SL_ATR_MULT = 1.5
WARMUP = max(EMA_SLOW + 2, 60)

# ── Ordered stage keys ────────────────────────────────────────────────────────
STAGE_KEYS = [
    "1_warmup",
    "2_session",
    "3_volatility",
    "4_signal_engine",
    "5_ema200_trend",
    "6_structural",
    "7_secondary",
    "8_construction",
    "9_ai_validator",
    "10_execute",
]

STAGE_LABELS = {
    "1_warmup":        "Warmup (insufficient bars)",
    "2_session":       "Session Gate",
    "3_volatility":    "Volatility Filter",
    "4_signal_engine": "Signal Engine (EMA×RSI)",
    "5_ema200_trend":  "EMA-200 Trend Filter",
    "6_structural":    "Structural Guards (BOS/FVG/tick/liq/VWAP)",
    "7_secondary":     "Secondary Filters (MACD/BB/ADX/Swing)",
    "8_construction":  "Trade Construction (SL/TP)",
    "9_ai_validator":  "AI Validator",
    "10_execute":      "EXECUTE",
}

# ── Mode definitions ──────────────────────────────────────────────────────────
#
# Filters mirror what the strategy card configures:
#   algo_only  — full structural guards, no AI gate
#   algo_ai    — full structural guards + AI validation
#   ai_signal  — AI handles structural (bos/fvg/swing dropped), AI gate with
#                a lower EMA-spread bar (LLM can act on weaker momentum)
#
MODES: dict[str, dict] = {
    "algo_only": {
        "description": "Pure algo — all structural guards, no AI gate",
        "filters": {
            "session_filter", "volatility_filter", "ema200_filter",
            "bos_guard", "fvg_guard", "tick_jump_guard", "liq_vacuum_guard",
            "vwap_guard", "macd_filter", "bollinger_filter", "adx_filter",
            "swing_structure",
        },
        "ai_gate": False,
        "ai_spread_min": None,
    },
    "algo_ai": {
        "description": "Algo + AI — all structural guards + AI validation gate",
        "filters": {
            "session_filter", "volatility_filter", "ema200_filter",
            "bos_guard", "fvg_guard", "tick_jump_guard", "liq_vacuum_guard",
            "vwap_guard", "macd_filter", "bollinger_filter", "adx_filter",
            "swing_structure",
        },
        "ai_gate": True,
        "ai_spread_min": 0.004,   # 0.4 % EMA spread — AI proxy (strong trend)
    },
    "ai_signal": {
        "description": "AI signal — fewer structural guards, AI gate (lower bar)",
        "filters": {
            "session_filter", "volatility_filter", "ema200_filter",
            "tick_jump_guard", "liq_vacuum_guard", "vwap_guard",
            # bos_guard / fvg_guard / swing_structure omitted — AI handles those
        },
        "ai_gate": True,
        "ai_spread_min": 0.002,   # 0.2 % EMA spread — AI can act on softer signals
    },
}


# ── Stage stats container ─────────────────────────────────────────────────────

@dataclass
class StageStats:
    candidates_in: int = 0
    blocked: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def block(self, reason: str) -> None:
        self.blocked += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


# ── Per-bar replay (mirrors make_signal_fn exactly) ───────────────────────────

def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, i: int, price: float) -> float:
    atr_period = min(14, i)
    h_sl = highs[i - atr_period: i + 1]
    l_sl = lows[i - atr_period: i + 1]
    c_pr = closes[max(0, i - atr_period - 1): i]
    ml = min(len(h_sl), len(l_sl), len(c_pr))
    h_sl, l_sl, c_pr = h_sl[-ml:], l_sl[-ml:], c_pr[-ml:]
    tr = np.maximum(h_sl - l_sl, np.abs(h_sl - c_pr))
    return float(np.mean(tr)) if len(tr) > 0 else price * 0.01


def replay_bar(
    i: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    timestamps: list,
    mode_cfg: dict,
    stats: dict[str, StageStats],
    ind: dict,
    sig_logic: str = "OR",
) -> str:
    """
    Run bar i through the staged filter chain.
    Returns the key of the stage where it was blocked, or '10_execute'.
    """
    filters = mode_cfg["filters"]
    ai_gate = mode_cfg["ai_gate"]
    ai_spread_min = mode_cfg["ai_spread_min"]

    # ── Stage 1: Warmup ───────────────────────────────────────────────────────
    stats["1_warmup"].candidates_in += 1
    if i < WARMUP:
        stats["1_warmup"].block("insufficient_bars")
        return "1_warmup"
    if np.isnan(ind["ema_f"][i]) or np.isnan(ind["ema_s"][i]):
        stats["1_warmup"].block("ema_nan")
        return "1_warmup"
    if i + 1 >= len(opens):
        stats["1_warmup"].block("no_next_bar")
        return "1_warmup"

    price = float(opens[i + 1])
    atr = _atr(highs, lows, closes, i, price)
    rsi_val = float(ind["rsi"][i])

    # ── Stage 2: Session Gate ─────────────────────────────────────────────────
    stats["2_session"].candidates_in += 1
    if "session_filter" in filters:
        try:
            ts = timestamps[i] if timestamps is not None and i < len(timestamps) else None
            bar_hour = ts.hour if ts is not None and hasattr(ts, "hour") else 12
            if get_session_score_for_hour(bar_hour) < 0.50:
                stats["2_session"].block("low_session_score")
                return "2_session"
        except Exception:
            pass

    # ── Stage 3: Volatility Filter ────────────────────────────────────────────
    stats["3_volatility"].candidates_in += 1
    if "volatility_filter" in filters and price > 0:
        atr_pct = (atr / price) * 100
        if atr_pct > 2.5:
            stats["3_volatility"].block("atr_too_high")
            return "3_volatility"
        if atr_pct < 0.05:
            stats["3_volatility"].block("atr_too_low")
            return "3_volatility"

    # ── Stage 4: Signal Engine ────────────────────────────────────────────────
    # Default: ema_crossover only (mirrors runner.py default signal_rules=[{source:ema_crossover}]).
    # rsi_reversal is added as a second OR-combined source to let more candidates
    # through to the structural stages — this shows the funnel at each downstream stage.
    stats["4_signal_engine"].candidates_in += 1
    rule_results = [
        check_ema_crossover(
            float(ind["ema_f"][i]), float(ind["ema_f"][i - 1]),
            float(ind["ema_s"][i]), float(ind["ema_s"][i - 1]),
            rsi_val, RSI_OB, RSI_OS,
        ),
        check_rsi_reversal(
            rsi_val, float(ind["rsi"][i - 1]), RSI_OB, RSI_OS,
        ),
    ]
    is_bull, is_bear = combine(rule_results, sig_logic)
    if not is_bull and not is_bear:
        stats["4_signal_engine"].block("no_directional_agreement")
        return "4_signal_engine"
    direction = "BUY" if is_bull else "SELL"

    # ── Stage 5: EMA-200 Trend Filter ────────────────────────────────────────
    stats["5_ema200_trend"].candidates_in += 1
    if "ema200_filter" in filters and "ema200" in ind:
        e200 = ind["ema200"][i]
        if not np.isnan(e200):
            if direction == "BUY" and price < e200:
                stats["5_ema200_trend"].block("price_below_ema200_on_buy")
                return "5_ema200_trend"
            if direction == "SELL" and price > e200:
                stats["5_ema200_trend"].block("price_above_ema200_on_sell")
                return "5_ema200_trend"

    # ── Stage 6: Structural Guards ────────────────────────────────────────────
    stats["6_structural"].candidates_in += 1
    if "bos_guard" in filters:
        bos = _detect_bos_simple(highs[max(0, i - 21): i + 1], lows[max(0, i - 21): i + 1])
        if direction == "BUY" and bos != "bullish":
            stats["6_structural"].block("bos_not_bullish")
            return "6_structural"
        if direction == "SELL" and bos != "bearish":
            stats["6_structural"].block("bos_not_bearish")
            return "6_structural"
    if "fvg_guard" in filters:
        if not _has_fvg(highs[max(0, i - 21): i + 1], lows[max(0, i - 21): i + 1], direction):
            stats["6_structural"].block("no_fvg_in_direction")
            return "6_structural"
    if "tick_jump_guard" in filters and i >= 2:
        move = abs(float(closes[i]) - float(closes[i - 2]))
        if atr > 0 and move / atr > 0.8:
            stats["6_structural"].block("tick_jump")
            return "6_structural"
    if "liq_vacuum_guard" in filters:
        bar_range = float(highs[i]) - float(lows[i])
        body = abs(float(opens[i]) - float(closes[i]))
        if bar_range > 0 and atr > 0 and bar_range / atr > 2.5 and (body / bar_range) * 100 < 30:
            stats["6_structural"].block("liq_vacuum_spike")
            return "6_structural"
    if "vwap_guard" in filters:
        dist = abs(price - float(ind["ema_f"][i]))
        if atr > 0 and dist / atr > 1.5:
            stats["6_structural"].block("vwap_distance")
            return "6_structural"

    # ── Stage 7: Secondary Filters ────────────────────────────────────────────
    stats["7_secondary"].candidates_in += 1
    if "macd_filter" in filters and i >= MACD_SLOW + MACD_SIGNAL_P and "macd_line" in ind:
        hist = float(ind["macd_line"][i]) - float(ind["macd_sig"][i])
        if not np.isnan(hist):
            if direction == "BUY" and hist < 0:
                stats["7_secondary"].block("macd_bearish_on_buy")
                return "7_secondary"
            if direction == "SELL" and hist > 0:
                stats["7_secondary"].block("macd_bullish_on_sell")
                return "7_secondary"
    if "bollinger_filter" in filters and "bb_pct_b" in ind and i >= BB_PERIOD:
        pb = float(ind["bb_pct_b"][i])
        if not np.isnan(pb):
            if direction == "BUY" and pb > 0.7:
                stats["7_secondary"].block("bb_overbought_on_buy")
                return "7_secondary"
            if direction == "SELL" and pb < 0.3:
                stats["7_secondary"].block("bb_oversold_on_sell")
                return "7_secondary"
    if "adx_filter" in filters and "adx_arr" in ind:
        adx_v = float(ind["adx_arr"][i])
        if not np.isnan(adx_v) and adx_v < ADX_MIN:
            stats["7_secondary"].block("adx_too_weak")
            return "7_secondary"
    if "swing_structure" in filters and i >= 20:
        swing = _swing_structure_simple(highs[max(0, i - 40): i + 1], lows[max(0, i - 40): i + 1])
        if direction == "BUY" and swing != "bullish":
            stats["7_secondary"].block("swing_not_bullish")
            return "7_secondary"
        if direction == "SELL" and swing != "bearish":
            stats["7_secondary"].block("swing_not_bearish")
            return "7_secondary"

    # ── Stage 8: Trade Construction ───────────────────────────────────────────
    stats["8_construction"].candidates_in += 1
    sl_dist = SL_ATR_MULT * atr
    if sl_dist <= 0 or price <= 0:
        stats["8_construction"].block("zero_sl_or_price")
        return "8_construction"

    # ── Stage 9: AI Validator (algo_ai / ai_signal only) ─────────────────────
    if ai_gate:
        stats["9_ai_validator"].candidates_in += 1
        ema_spread = abs(float(ind["ema_f"][i]) - float(ind["ema_s"][i])) / price if price > 0 else 0
        rsi_extreme = rsi_val > 75 or rsi_val < 25
        if ema_spread < ai_spread_min:
            stats["9_ai_validator"].block("ai_weak_trend_momentum")
            return "9_ai_validator"
        if rsi_extreme:
            stats["9_ai_validator"].block("ai_rsi_overextended")
            return "9_ai_validator"

    # ── Stage 10: Execute ─────────────────────────────────────────────────────
    stats["10_execute"].candidates_in += 1
    return "10_execute"


# ── Pre-compute all indicators once (same approach as make_signal_fn cache) ───

def build_indicators(opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> dict:
    ind: dict = {}
    ind["ema_f"] = _ema(closes, EMA_FAST)
    ind["ema_s"] = _ema(closes, EMA_SLOW)
    ind["rsi"] = _rsi(closes, RSI_PERIOD)
    ind["ema200"] = _ema(closes, 200)

    macd_f = _ema(closes, MACD_FAST)
    macd_s = _ema(closes, MACD_SLOW)
    ml = macd_f - macd_s
    ind["macd_line"] = ml
    ind["macd_sig"] = _ema_from_array(ml, MACD_SIGNAL_P)

    ind["bb_pct_b"] = _bollinger_pct_b(closes, BB_PERIOD, BB_STD)
    ind["adx_arr"], ind["plus_di"], ind["minus_di"] = _adx_arrays(highs, lows, closes, ADX_PERIOD)

    return ind


# ── Table printing ────────────────────────────────────────────────────────────

def _col(val: object, width: int, align: str = "right") -> str:
    s = str(val)
    if align == "right":
        return s.rjust(width)
    return s.ljust(width)


def print_funnel_table(mode_name: str, mode_cfg: dict, stats: dict[str, StageStats], total_bars: int, sig_logic: str = "OR") -> None:
    ai_gate = mode_cfg["ai_gate"]
    print(f"\n{'─' * 100}")
    print(f"  MODE: {mode_name.upper():<12}  {mode_cfg['description']}")
    print(f"  Total bars: {total_bars:,}  |  Warmup: {WARMUP} bars  |  Signal rules: [ema_crossover, rsi_reversal] ({sig_logic})")
    print(f"  AI gate: {'YES — EMA spread ≥ ' + str(mode_cfg['ai_spread_min']) if ai_gate else 'NO'}")
    print(f"{'─' * 100}")
    header = f"  {'Stage':<46} {'Cand In':>8} {'Blocked':>8} {'Passed':>8} {'Block%':>7}  Top Block Reason"
    print(header)
    print(f"  {'─'*46} {'─'*8} {'─'*8} {'─'*8} {'─'*7}  {'─'*30}")

    for key in STAGE_KEYS:
        s = stats[key]
        label = STAGE_LABELS[key]

        if key == "9_ai_validator" and not ai_gate:
            row = (
                f"  {label:<46} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'—':>7}  skipped (algo_only)"
            )
            print(row)
            continue

        cand_in = s.candidates_in
        blocked = s.blocked
        passed = cand_in - blocked if key != "10_execute" else cand_in
        block_pct = (blocked / cand_in * 100) if cand_in > 0 else 0
        top_reason = max(s.reasons, key=s.reasons.get) if s.reasons else "—"
        top_count = s.reasons.get(top_reason, 0) if top_reason != "—" else ""
        top_str = f"{top_reason} ({top_count})" if top_count else "—"

        block_pct_str = f"{block_pct:.1f}%" if cand_in > 0 else "—"

        row = (
            f"  {label:<46} {cand_in:>8,} {blocked:>8,} {passed:>8,} {block_pct_str:>7}  {top_str}"
        )
        print(row)

    final = stats["10_execute"].candidates_in
    pct_of_candidates = final / (total_bars - WARMUP) * 100 if total_bars > WARMUP else 0
    print(f"{'─' * 100}")
    print(f"  ► Final execute count: {final:,}  ({pct_of_candidates:.2f}% of post-warmup candidates)")


def print_block_reasons_table(mode_name: str, stats: dict[str, StageStats], ai_gate: bool) -> None:
    print(f"\n  Block reasons — {mode_name.upper()}")
    print(f"  {'Stage':<46} {'Reason':<35} {'Count':>6}")
    print(f"  {'─'*46} {'─'*35} {'─'*6}")
    printed = False
    for key in STAGE_KEYS:
        if key == "9_ai_validator" and not ai_gate:
            continue
        s = stats[key]
        for reason, count in sorted(s.reasons.items(), key=lambda x: -x[1]):
            label = STAGE_LABELS[key]
            print(f"  {label:<46} {reason:<35} {count:>6,}")
            printed = True
    if not printed:
        print("  (no blocks recorded)")


def print_comparison_table(all_stats: dict[str, dict[str, StageStats]], mode_cfgs: dict, days: int = 30) -> None:
    mode_names = list(all_stats.keys())
    print(f"\n{'═' * 100}")
    print("  COMPARISON — all 3 modes, same data window")
    print(f"{'═' * 100}")
    col_w = 17
    header = f"  {'Stage':<46}"
    for m in mode_names:
        header += f" {m:>{col_w}}"
    print(header)
    print(f"  {'─'*46}" + "".join(f" {'─'*col_w}" for _ in mode_names))

    for key in STAGE_KEYS:
        label = STAGE_LABELS[key]
        row = f"  {label:<46}"
        for m in mode_names:
            s = all_stats[m][key]
            ai_gate = mode_cfgs[m]["ai_gate"]
            if key == "9_ai_validator" and not ai_gate:
                cell = "N/A"
            elif key == "10_execute":
                cell = f"{s.candidates_in:,}"
            else:
                passed = s.candidates_in - s.blocked
                pct = (passed / s.candidates_in * 100) if s.candidates_in > 0 else 0
                cell = f"{passed:,} ({pct:.0f}%)"
            row += f" {cell:>17}"
        print(row)

    print(f"{'═' * 100}")

    # Highlight choke point and winner
    # Choke = stage with highest weighted-average absolute block count (not % — avoids
    # artificially crowning a stage that sees only 1 candidate at 100% block rate)
    stage_total_blocked: dict[str, int] = {}
    for key in STAGE_KEYS:
        if key in ("1_warmup", "10_execute"):
            continue
        total_blocked = sum(all_stats[m][key].blocked for m in mode_names)
        if total_blocked > 0:
            stage_total_blocked[key] = total_blocked

    if stage_total_blocked:
        choke = max(stage_total_blocked, key=stage_total_blocked.get)
        choke_pcts = []
        for m in mode_names:
            s = all_stats[m][choke]
            if s.candidates_in > 0:
                choke_pcts.append(s.blocked / s.candidates_in * 100)
        avg_pct = sum(choke_pcts) / len(choke_pcts) if choke_pcts else 0
        print(f"\n  ◆ MAIN CHOKE POINT: {STAGE_LABELS[choke]}")
        print(f"    Total blocked (all modes): {stage_total_blocked[choke]:,}  |  Avg block rate: {avg_pct:.1f}%")

    # Mode that executes most (or most permissive if all tied at 0)
    execute_counts = {m: all_stats[m]["10_execute"].candidates_in for m in mode_names}
    max_exe = max(execute_counts.values())
    if max_exe > 0:
        winner = max(execute_counts, key=execute_counts.get)
        print(f"  ◆ MOST EXECUTIONS:  {winner.upper()} ({execute_counts[winner]:,} trades through to execute)")
    else:
        # All tied at 0 — show which mode lets most through to the latest stage
        stage_depth: dict[str, int] = {}
        for m in mode_names:
            for idx, key in enumerate(reversed(STAGE_KEYS)):
                s = all_stats[m][key]
                if s.candidates_in > 0:
                    stage_depth[m] = len(STAGE_KEYS) - idx
                    break
            else:
                stage_depth[m] = 0
        most_permissive = max(stage_depth, key=stage_depth.get)
        deepest_stage = STAGE_LABELS[STAGE_KEYS[stage_depth[most_permissive] - 1]]
        print(f"  ◆ MOST PERMISSIVE:  {most_permissive.upper()} (0 executes all modes — deepest stage reached: {deepest_stage})")
        print(f"    NOTE: 0 final executes across all modes — signal engine + BOS guard blocked all candidates")
        print(f"          in this {days}-day window. Try a longer window (--days 90) for a trending period.")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTC-USD", help="yfinance ticker (default: BTC-USD)")
    parser.add_argument("--days", type=int, default=30, help="lookback days (default: 30)")
    parser.add_argument("--logic", default="OR", choices=["AND", "OR", "MAJORITY"],
                        help="signal combine logic (default: OR)")
    args = parser.parse_args()

    symbol = args.symbol
    days = args.days
    sig_logic = args.logic

    print(f"\n{'═' * 100}")
    print(f"  ALPHALOOP — Replay Simulation  |  3 Signal Modes  |  Pipeline Funnel")
    print(f"  Symbol: {symbol}  |  Window: {days} days  |  Timeframe: 1h")
    print(f"  Signal rules: [ema_crossover, rsi_reversal]  |  Logic: {sig_logic}")
    print(f"  Source: yfinance  |  Files: backtester/runner.py, signals/conditions.py")
    print(f"{'═' * 100}")

    print(f"\n  Fetching {days}d of {symbol} hourly data from yfinance...", flush=True)
    df = yf.download(symbol, period=f"{days}d", interval="1h", auto_adjust=True, progress=False)

    if df.empty:
        print("  ERROR: yfinance returned no data. Check symbol/connectivity.", file=sys.stderr)
        sys.exit(1)

    opens = np.array(df["Open"].values, dtype=float).flatten()
    highs = np.array(df["High"].values, dtype=float).flatten()
    lows = np.array(df["Low"].values, dtype=float).flatten()
    closes = np.array(df["Close"].values, dtype=float).flatten()
    timestamps = list(df.index.to_pydatetime())

    total_bars = len(closes)
    date_start = df.index[0].strftime("%Y-%m-%d")
    date_end = df.index[-1].strftime("%Y-%m-%d")
    print(f"  Loaded {total_bars:,} bars  |  {date_start} → {date_end}")

    print("  Pre-computing indicators (EMA/RSI/MACD/BB/ADX)...", flush=True)
    ind = build_indicators(opens, highs, lows, closes)

    all_stats: dict[str, dict[str, StageStats]] = {}

    for mode_name, mode_cfg in MODES.items():
        print(f"  Running replay: {mode_name}...", flush=True)
        stats: dict[str, StageStats] = {key: StageStats() for key in STAGE_KEYS}
        for i in range(total_bars):
            replay_bar(i, opens, highs, lows, closes, timestamps, mode_cfg, stats, ind, sig_logic)
        all_stats[mode_name] = stats

    # ── Print per-mode funnel tables ──────────────────────────────────────────
    for mode_name, mode_cfg in MODES.items():
        print_funnel_table(mode_name, mode_cfg, all_stats[mode_name], total_bars, sig_logic)

    # ── Print block reasons tables ────────────────────────────────────────────
    print(f"\n{'─' * 100}")
    print("  BLOCK REASONS (all modes)")
    print(f"{'─' * 100}")
    for mode_name, mode_cfg in MODES.items():
        print_block_reasons_table(mode_name, all_stats[mode_name], mode_cfg["ai_gate"])
        print()

    # ── Print comparison table ────────────────────────────────────────────────
    print_comparison_table(all_stats, MODES, days)


if __name__ == "__main__":
    main()
