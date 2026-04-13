"""
Gate-2 pipeline mode comparison integration test.

Runs a realistic BUY pullback signal through Stage 4B (quality scoring) and
Stage 5 (conviction) and demonstrates the Gate-2 fix: tools that previously
scored 0-25 for normal market conditions now score 50-95.

Also compares all 3 signal modes (algo_only, algo_ai, ai_signal) to show how
each mode's AI validator behaviour differs.

Gate-1 baseline: 89/89 reached Stage 5 → 89 HELD (100%). Top score: 53.1.
Gate-2 target:   same signals score 70-95 → conviction TRADE.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from alphaloop.pipeline.conviction import ConvictionScorer
from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
from alphaloop.pipeline.invalidation import StructuralInvalidator
from alphaloop.pipeline.market_gate import MarketGate
from alphaloop.pipeline.orchestrator import PipelineOrchestrator
from alphaloop.pipeline.quality import StructuralQuality
from alphaloop.pipeline.regime import RegimeClassifier
from alphaloop.pipeline.risk_gate import RiskGateRunner
from alphaloop.pipeline.types import CandidateSignal, CycleOutcome, RegimeSnapshot
from alphaloop.tools.registry import STAGE_TOOL_MAP, ToolRegistry

# ---------------------------------------------------------------------------
# Tools fixed in Gate-2 — these must score >= 50 on neutral/normal conditions
# ---------------------------------------------------------------------------
_GATE2_FIXED_TOOLS = {
    "rsi_feature",       # CRITICAL: RSI=50 was 16.7 (3-feature avg bug)
    "bos_guard",         # CRITICAL: no BOS was 0.0 (should be 50 neutral)
    "fvg_guard",         # CRITICAL: no FVG was 0.0 (should be 50 neutral)
    "alma_filter",       # HIGH: at ALMA was 0.0 (inverted scale)
    "ema_crossover",     # HIGH: fresh crossover was 0.0 (inverted scale)
    "bollinger_filter",  # HIGH: BUY at lower band was 0.0 (direction-agnostic bug)
    "trendilo",          # HIGH: flat market was 0.0 (should be 50 neutral)
    "volatility_filter", # HIGH: unavailable was 0.0 (should be 50 neutral)
    "session_filter",    # MINOR: asia_early was 20.0 (floor raised to 30)
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pullback_context(direction: str = "BUY") -> MagicMock:
    """
    Realistic BUY pullback context — the scenario that Gate-1 held 100% of
    the time due to tool scoring bugs.

    Conditions that SHOULD score well but scored 0-25 before Gate-2:
      - RSI=55    (healthy BUY momentum — old 3-feature avg → 21.7)
      - No BOS    (pullback setup by definition — old score → 0.0)
      - No FVG    (common neutral condition — old score → 0.0)
      - ALMA at price (ideal proximity — old inverted score → 0.0)
      - EMA at fresh crossover (the trigger — old spread score → 0.0)
      - BB pct_B = 0.35 (below midline, good for BUY — old bb_position=35)
      - Flat trend (ranging → old trendilo strength=0 → 0.0)
      - Normal volatility (ATR 0.3% — stored as 0.3 percentage units)
      - London session score=0.85 (active — OK before and after)

    Note: atr_pct is stored as a percentage (0.3 = 0.3%), not a ratio (0.003).
    """
    ctx = MagicMock()
    ctx.symbol = "XAUUSD"
    ctx.trade_direction = direction
    ctx.pip_size = 0.01

    ctx.indicators = {
        "M15": {
            # Price / structure
            "ema200": 3090.0,
            "ema_fast": 3100.5,   # fresh crossover: fast ≈ slow
            "ema_slow": 3099.8,
            "alma": 3100.0,       # price sitting at ALMA (ideal proximity)
            "atr": 10.0,
            # RSI=55: healthy BUY momentum (was 21.7 before fix)
            "rsi": 55.0,
            # ADX=45: moderate trend (adx_strength=45, neutral-ish; not a Gate-2 fix)
            "adx": 45.0,
            "adx_plus_di": 28.0,
            "adx_minus_di": 18.0,
            "choppiness": 38.0,
            # MACD: positive crossover
            "macd": 0.6,
            "macd_signal": 0.2,
            "macd_hist": 0.4,
            # Bollinger: pct_B=0.35 — below midline, good for BUY
            "bb_upper": 3115.0,
            "bb_middle": 3100.0,
            "bb_lower": 3085.0,
            "bb_pct_b": 0.35,
            "bb_band_width": 30.0,
            # BOS: NONE — pullback setup doesn't have a fresh BOS
            # Before Gate-2: scored 0.0 (false contradiction)
            # After Gate-2:  scores 50.0 (neutral)
            "bos": {
                "bullish_bos": False,
                "bullish_break_atr": 0.0,
                "bearish_bos": False,
                "bearish_break_atr": 0.0,
                "swing_high": 3108.0,
                "swing_low": 3085.0,
            },
            # FVG: NONE — common neutral condition
            # Before Gate-2: scored 0.0/0.0 (false contradiction)
            # After Gate-2:  scores 50.0/50.0 (neutral)
            "fvg": {
                "bullish": [],
                "bearish": [],
            },
            # VWAP — price above VWAP (bullish)
            "vwap": 3095.0,
            "swing_structure": "bullish",
            # Volume — slightly above average
            "volume": 1200.0,
            "volume_ma": 1000.0,
            "volume_ratio": 1.2,
            # Microstructure — clean
            "fast_fingers": {
                "is_exhausted_up": False,
                "is_exhausted_down": False,
                "exhaustion_score": 15,
            },
            "tick_jump_atr": 0.15,
            "liq_vacuum": {"bar_range_atr": 0.7, "body_pct": 60},
            "median_spread": 1.5,
        },
        "H1": {
            # atr_pct is stored as percentage units (0.3 = 0.3%); the tool
            # compares against min_atr_pct=0.05% and max_atr_pct=2.5%.
            # Old test had 0.003 (ratio), causing "dead market" (score=1.2).
            "atr_pct": 0.3,
            "atr": 30.0,
            "ema200": 3085.0,
            "ema_fast": 3095.0,
            "ema_slow": 3090.0,
        },
    }

    ctx.session = MagicMock(is_weekend=False, score=0.85, name="london")
    ctx.price = MagicMock(
        bid=3100.5,
        ask=3101.0,
        spread=1.5,
        time=datetime.now(timezone.utc),
    )
    ctx.news = []
    # DXY / sentiment as dicts (some tools read via .get())
    ctx.dxy = {"value": 103.5, "direction": "neutral", "strength": 0.0, "bias": "neutral"}
    ctx.sentiment = {"score": 0.05, "direction": "neutral", "bias": "neutral"}
    ctx.open_trades = {}
    ctx.risk_monitor = SimpleNamespace(
        kill_switch_active=False,
        _kill_switch_active=False,
        _open_risk_usd=0,
        account_balance=10000,
        can_open_trade=AsyncMock(return_value=(True, "")),
    )
    ctx.df = MagicMock(__len__=lambda self: 500)
    ctx.tool_results = []
    return ctx


def _make_regime() -> RegimeSnapshot:
    """Regime snapshot that ConvictionScorer.score() accepts."""
    return RegimeSnapshot(
        regime="trending",
        macro_regime="risk_on",
        volatility_band="normal",
        allowed_setups=["pullback"],
    )


def _get_quality_tools() -> list:
    registry = ToolRegistry()
    names = STAGE_TOOL_MAP.get("quality", [])
    return [t for name in names if (t := registry.get_tool(name)) is not None]


def _make_signal(direction: str = "BUY") -> CandidateSignal:
    return CandidateSignal(
        direction=direction,
        setup_type="pullback",
        entry_zone=(3100.0, 3102.0),
        stop_loss=3090.0,
        take_profit=[3115.0],
        raw_confidence=0.72,
        rr_ratio=1.5,
        signal_sources=["ema_crossover"],
        reasoning="BUY pullback at ALMA / EMA crossover",
    )


# ---------------------------------------------------------------------------
# Stage 4B: Quality Scoring — Gate-2 tool score validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate2_fixed_tools_no_false_contradictions():
    """
    Gate-2 fix: all 9 previously-buggy tools must score >= 50 on a valid
    pullback BUY signal (neutral/normal market conditions).

    Before Gate-2:
      rsi_feature:      21.7  (3-feature avg bug, RSI=55)
      bos_guard:         0.0  (no BOS = catastrophic, should be neutral)
      fvg_guard:         0.0  (no FVG = catastrophic, should be neutral)
      alma_filter:      25.0  (inverted scale, at ALMA = worst)
      ema_crossover:    25.0  (inverted scale, fresh crossover = worst)
      bollinger_filter: 17.5  (direction-agnostic, BUY at lower band = 0)
      trendilo:         25.0  (flat market scores 0, not 50 neutral)
      volatility_filter: 1.2  (0.3% ATR miscalculated as dead market)
      session_filter:   85.0  (london OK before too — unchanged)
    """
    tools = _get_quality_tools()
    quality = StructuralQuality(tools=tools)
    ctx = _make_pullback_context(direction="BUY")

    result = await quality.evaluate(ctx)

    # Only check tools that Gate-2 fixed — other tools may legitimately score
    # below 50 for certain market conditions (e.g. ADX=45 → adx_strength=45)
    contradictions = {
        name: score
        for name, score in result.tool_scores.items()
        if name in _GATE2_FIXED_TOOLS and score < 50.0
    }

    assert not contradictions, (
        f"Gate-2 FAILED: {len(contradictions)} previously-fixed tool(s) still "
        f"score below neutral 50 on a valid pullback BUY signal:\n"
        + "\n".join(f"  {name}: {score:.1f}" for name, score in sorted(contradictions.items()))
        + f"\nAll tool scores: {result.tool_scores}"
    )


@pytest.mark.asyncio
async def test_gate2_tool_scores_comparison_table():
    """
    Print a Gate-1 vs Gate-2 comparison table for audit.
    Shows what each fixed tool now scores vs what it scored before.
    """
    tools = _get_quality_tools()
    quality = StructuralQuality(tools=tools)
    ctx = _make_pullback_context(direction="BUY")

    result = await quality.evaluate(ctx)

    # Gate-1 known-bad scores for this pullback BUY context
    gate1_scores = {
        "rsi_feature":        21.7,  # CRITICAL false contradiction
        "bos_guard":           0.0,  # CRITICAL false contradiction
        "fvg_guard":           0.0,  # CRITICAL false contradiction
        "alma_filter":        25.0,  # inverted: 0=at ALMA with 50 trend avg
        "ema_crossover":      25.0,  # 0=fresh crossover with 50 alignment avg
        "bollinger_filter":   17.5,  # pct_B=0.35 → 35, not direction-aware (avg with bw)
        "trendilo":           25.0,  # 0=flat + 50 direction / 2
        "volatility_filter":   1.2,  # 0.3% ATR stored as ratio 0.003 → dead market
        "session_filter":     85.0,  # london 0.85, was OK — unchanged
    }

    header = f"\n{'Tool':<25} {'Gate-1':>8} {'Gate-2':>8} {'Delta':>8}  Status"
    rows = [header, "  " + "-" * 64]
    fixed_count = 0

    for tool_name in sorted(gate1_scores):
        g1 = gate1_scores[tool_name]
        g2 = result.tool_scores.get(tool_name)
        if g2 is None:
            rows.append(f"  {tool_name:<23} {g1:>8.1f} {'N/A':>8}  (not in quality stage)")
            continue
        delta = g2 - g1
        if g1 < 25.0 and g2 >= 50.0:
            status = "FIXED (was false contradiction)"
            fixed_count += 1
        elif g1 < 50.0 and g2 >= 50.0:
            status = "FIXED"
            fixed_count += 1
        elif g2 < 50.0 and tool_name in _GATE2_FIXED_TOOLS:
            status = "REGRESSION"
        else:
            status = "ok"
        rows.append(f"  {tool_name:<23} {g1:>8.1f} {g2:>8.1f} {delta:>+8.1f}  {status}")

    rows += [
        "  " + "-" * 64,
        f"  Group scores: {result.group_scores}",
        f"  Overall:      {result.overall_score:.1f}  (floor=55.0)",
    ]

    print("\n" + "\n".join(rows))

    assert fixed_count >= 3, f"Expected >= 3 Gate-2 tools fixed, got {fixed_count}"
    assert result.overall_score >= 55.0, (
        f"overall_score {result.overall_score:.1f} < 55.0 after Gate-2 fixes"
    )


@pytest.mark.asyncio
async def test_gate2_overall_score_above_quality_floor():
    """After Gate-2, overall_score must be >= 55.0 (the quality floor)."""
    tools = _get_quality_tools()
    quality = StructuralQuality(tools=tools)
    ctx = _make_pullback_context(direction="BUY")

    result = await quality.evaluate(ctx)

    assert result.overall_score >= 55.0, (
        f"Gate-2 FAILED: overall_score={result.overall_score:.1f} < floor=55.0. "
        f"Group scores: {result.group_scores}. Tool scores: {result.tool_scores}"
    )


# ---------------------------------------------------------------------------
# Stage 5: Conviction — Gate-2 eliminates quality floor HOLD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate2_conviction_reaches_trade():
    """
    Gate-1: 89/89 signals held because overall_score < 55 (quality floor).
    Gate-2: same market context produces a TRADE decision (floor not triggered).
    """
    tools = _get_quality_tools()
    quality = StructuralQuality(tools=tools)
    ctx = _make_pullback_context(direction="BUY")

    quality_result = await quality.evaluate(ctx)

    scorer = ConvictionScorer()
    conviction = scorer.score(quality_result, _make_regime())

    conv_score = f"{conviction.score:.1f}"
    print(
        f"\n  Gate-2 Conviction Result:"
        f"\n    overall_score:        {quality_result.overall_score:.1f}"
        f"\n    quality_floor_hit:    {conviction.quality_floor_triggered}"
        f"\n    conviction_score:     {conv_score}"
        f"\n    decision:             {conviction.decision}"
        f"\n    hold_reason:          {conviction.hold_reason}"
        f"\n    group_contributions:  {conviction.group_contributions}"
    )

    assert not conviction.quality_floor_triggered, (
        f"Gate-2 FAILED: quality floor still triggered. "
        f"overall={quality_result.overall_score:.1f}, "
        f"reason={conviction.hold_reason}"
    )
    assert conviction.decision == "TRADE", (
        f"Gate-2 FAILED: conviction HELD. "
        f"score={conv_score}, reason={conviction.hold_reason}"
    )


# ---------------------------------------------------------------------------
# Mode comparison: algo_only vs algo_ai vs ai_signal
# ---------------------------------------------------------------------------


class TestGate2ModeComparison:
    """
    Side-by-side comparison of all 3 signal modes after Gate-2 fixes.

    Quality scoring (Stage 4B) and conviction (Stage 5) are identical across
    modes — only Stage 6 (AI Validator) differs:

      algo_only:            AI validator not invoked — Stage 5 is final
      algo_ai (approve):    AI validates algo signal; approves → TRADE
      algo_ai (reject):     AI validates algo signal; reject → hard block
      ai_signal (soft):     AI generates + validates; reject → -0.15 soft penalty
    """

    def _make_orchestrator(self) -> PipelineOrchestrator:
        return PipelineOrchestrator(
            market_gate=MarketGate(stale_bar_seconds=600, min_bars_required=50),
            regime_classifier=RegimeClassifier(),
            invalidator=StructuralInvalidator(cfg={"sl_min_points": 100, "sl_max_points": 3000}),
            quality_scorer=StructuralQuality(tools=_get_quality_tools()),
            conviction_scorer=ConvictionScorer(),
            risk_gate=RiskGateRunner(),
            execution_guard=ExecutionGuardRunner(),
        )

    @staticmethod
    def _run(orchestrator, ctx, mode: str, validator=None) -> object:
        async def _go():
            async def signal_gen(context, regime):
                return _make_signal()

            if validator is not None:
                orchestrator.ai_validator = validator
            return await orchestrator.run(ctx, signal_gen, symbol="XAUUSD", mode=mode)

        return asyncio.run(_go())

    def test_algo_only_mode_reaches_trade(self):
        """
        algo_only: no AI involved. Gate-2 fixes mean Stage 5 produces TRADE.
        """
        ctx = _make_pullback_context()
        result = self._run(self._make_orchestrator(), ctx, mode="algo_only")

        conv_score = f"{result.conviction.score:.1f}" if result.conviction else "N/A"
        conv_dec = result.conviction.decision if result.conviction else "N/A"
        qual_score = f"{result.quality.overall_score:.1f}" if result.quality else "N/A"
        print(
            f"\n  [algo_only]"
            f"\n    outcome:          {result.outcome}"
            f"\n    conviction_score: {conv_score}"
            f"\n    conviction_dec:   {conv_dec}"
            f"\n    quality_overall:  {qual_score}"
        )

        assert result.outcome == CycleOutcome.TRADE_OPENED, (
            f"[algo_only] Expected TRADE_OPENED, got {result.outcome}. "
            f"Conviction: {result.conviction}"
        )
        assert result.conviction is not None
        assert result.conviction.decision == "TRADE"
        assert not result.conviction.quality_floor_triggered

    def test_algo_ai_mode_passes_when_ai_approves(self):
        """
        algo_ai: AI approves → TRADE_OPENED (same as algo_only).
        """
        ctx = _make_pullback_context()
        orchestrator = self._make_orchestrator()

        mock_validator = MagicMock()
        async def approve(signal, regime, quality, conviction, context, *, mode="algo_ai"):
            return signal  # pass-through approval
        mock_validator.validate = approve

        result = self._run(orchestrator, ctx, mode="algo_ai", validator=mock_validator)

        conv_score = f"{result.conviction.score:.1f}" if result.conviction else "N/A"
        print(
            f"\n  [algo_ai] AI approves"
            f"\n    outcome:          {result.outcome}"
            f"\n    conviction_score: {conv_score}"
        )

        assert result.outcome == CycleOutcome.TRADE_OPENED
        assert result.conviction is not None
        assert result.conviction.decision == "TRADE"

    def test_algo_ai_mode_hard_blocks_when_ai_rejects(self):
        """
        algo_ai: AI rejects → hard block (HELD or REJECTED), signal discarded.
        This behaviour is unchanged by Gate-2.
        """
        ctx = _make_pullback_context()
        orchestrator = self._make_orchestrator()

        mock_validator = MagicMock()
        mock_validator.validate = AsyncMock(return_value=None)  # hard reject

        result = self._run(orchestrator, ctx, mode="algo_ai", validator=mock_validator)

        print(
            f"\n  [algo_ai] AI hard-rejects"
            f"\n    outcome:          {result.outcome}"
            f"\n    rejection_reason: {result.rejection_reason}"
        )

        assert result.outcome in (CycleOutcome.HELD, CycleOutcome.REJECTED), (
            f"[algo_ai] AI hard-reject should HOLD/REJECT, got {result.outcome}"
        )

    def test_ai_signal_mode_soft_veto_preserves_signal(self):
        """
        ai_signal: AI advisory reject → soft -0.15 confidence, NOT hard block.
        Gate-2 change: AIValidator in ai_signal mode returns reduced-conf signal.
        """
        ctx = _make_pullback_context()
        orchestrator = self._make_orchestrator()

        mock_validator = MagicMock()
        async def soft_reject(signal, regime, quality, conviction, context, *, mode="algo_ai"):
            if mode == "ai_signal":
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
            return None  # algo_ai hard-reject (unchanged)
        mock_validator.validate = soft_reject

        result = self._run(orchestrator, ctx, mode="ai_signal", validator=mock_validator)

        conv_score = f"{result.conviction.score:.1f}" if result.conviction else "N/A"
        conv_dec = result.conviction.decision if result.conviction else "N/A"
        print(
            f"\n  [ai_signal] AI advisory reject (soft)"
            f"\n    outcome:           {result.outcome}"
            f"\n    conviction_score:  {conv_score}"
            f"\n    conviction_dec:    {conv_dec}"
        )

        # Soft veto — signal must NOT have been hard-killed
        assert result.outcome != CycleOutcome.NO_SIGNAL, (
            "ai_signal soft veto should not produce NO_SIGNAL"
        )

    def test_mode_comparison_summary_table(self):
        """
        Full side-by-side table: all 3 modes (+ variants) after Gate-2.
        Validates gate-2 postconditions and prints audit data.
        """

        async def _run_all():
            async def signal_gen(context, regime):
                return _make_signal()

            scenarios = {}

            # --- algo_only ---
            orch = self._make_orchestrator()
            ctx = _make_pullback_context()
            scenarios["algo_only"] = await orch.run(
                ctx, signal_gen, symbol="XAUUSD", mode="algo_only"
            )

            # --- algo_ai (AI approve) ---
            orch = self._make_orchestrator()
            ctx = _make_pullback_context()
            mv = MagicMock()
            async def approve(sig, reg, qual, conv, ctx, *, mode="algo_ai"):
                return sig
            mv.validate = approve
            orch.ai_validator = mv
            scenarios["algo_ai (approve)"] = await orch.run(
                ctx, signal_gen, symbol="XAUUSD", mode="algo_ai"
            )

            # --- algo_ai (AI reject) ---
            orch = self._make_orchestrator()
            ctx = _make_pullback_context()
            mv2 = MagicMock()
            mv2.validate = AsyncMock(return_value=None)
            orch.ai_validator = mv2
            scenarios["algo_ai (reject)"] = await orch.run(
                ctx, signal_gen, symbol="XAUUSD", mode="algo_ai"
            )

            # --- ai_signal (soft veto) ---
            orch = self._make_orchestrator()
            ctx = _make_pullback_context()
            mv3 = MagicMock()
            async def soft(sig, reg, qual, conv, ctx, *, mode="algo_ai"):
                if mode == "ai_signal":
                    soft_conf = round(max(0.30, sig.raw_confidence - 0.15), 4)
                    return CandidateSignal(
                        direction=sig.direction, setup_type=sig.setup_type,
                        entry_zone=sig.entry_zone, stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit, raw_confidence=soft_conf,
                        rr_ratio=sig.rr_ratio, signal_sources=sig.signal_sources,
                        reasoning=sig.reasoning,
                    )
                return None
            mv3.validate = soft
            orch.ai_validator = mv3
            scenarios["ai_signal (soft)"] = await orch.run(
                ctx, signal_gen, symbol="XAUUSD", mode="ai_signal"
            )

            return scenarios

        scenarios = asyncio.run(_run_all())

        # --- Print comparison table ---
        print("\n" + "=" * 76)
        print("  Gate-2 Mode Comparison — BUY pullback (RSI=55, no BOS, no FVG)")
        print("  Gate-1 baseline: 100% held (89/89). Fix: 10 tool scoring bugs.")
        print("=" * 76)
        print(f"  {'Mode':<30} {'Outcome':<20} {'Conv':>6} {'Decision':<10} {'Floor?'}")
        print("  " + "-" * 70)

        for label, r in scenarios.items():
            conv_score = f"{r.conviction.score:.1f}" if r.conviction else "N/A"
            conv_dec = r.conviction.decision if r.conviction else "N/A"
            floor = str(r.conviction.quality_floor_triggered) if r.conviction else "N/A"
            print(
                f"  {label:<30} {r.outcome.value:<20} {conv_score:>6} "
                f"{conv_dec:<10} {floor}"
            )

        # Group scores (same for all modes — Stage 4B runs before mode diverges)
        first_result = scenarios["algo_only"]
        if first_result.quality:
            qual = first_result.quality
            print()
            print("  Stage 4B Group Scores (identical across all modes):")
            for group, score in sorted(qual.group_scores.items()):
                bar = "#" * int(score / 5)
                print(f"    {group:<12} {score:5.1f}  {bar}")
            print(f"    {'overall':<12} {qual.overall_score:5.1f}  (floor=55.0)")

        print("=" * 76)

        # Gate-2 postcondition: algo_only must reach TRADE_OPENED
        algo_only = scenarios["algo_only"]
        assert algo_only.outcome == CycleOutcome.TRADE_OPENED, (
            f"Gate-2 FAILED for algo_only: {algo_only.outcome}. "
            f"Conviction: {algo_only.conviction}"
        )

        # AI hard-reject in algo_ai must NOT reach TRADE_OPENED
        ai_hard = scenarios["algo_ai (reject)"]
        assert ai_hard.outcome != CycleOutcome.TRADE_OPENED, (
            "algo_ai hard-reject should not produce TRADE_OPENED"
        )

        # ai_signal soft veto: signal should not be killed (NO_SIGNAL)
        ai_soft = scenarios["ai_signal (soft)"]
        assert ai_soft.outcome != CycleOutcome.NO_SIGNAL, (
            "ai_signal soft veto should not produce NO_SIGNAL"
        )
