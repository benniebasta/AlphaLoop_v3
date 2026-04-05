# Trading Modes — algo_only / algo_ai / ai_signal

All three modes share the same 8-stage pipeline. The difference is which engine runs
at Stage 3 (direction hypothesis) and whether AI validation runs at Stage 6.

---

## The Shared Spine — 8 stages, every mode runs these

```
Stage 1  → Market Gate        (spread, hours, news)
Stage 2  → Regime Classifier  (trending / ranging / volatile / dead)
Stage 3  → Direction Engine   ← MODE SPLITS HERE
Stage 3B → Trade Constructor  (structure-derived SL/TP)
Stage 4A → Structural Invalidation (hard safety-net)
Stage 4B → Structural Quality (soft scoring)
Stage 5  → Conviction Scorer  (TRADE or HOLD decision)
Stage 6  → AI Validator       ← MODE SPLITS AGAIN
Stage 7  → Risk Gate          (position sizing, portfolio risk)
Stage 8  → Execution Guard    (tick-jump, dedup, delay/execute/block)
```

---

## Mode 1 — `algo_only`

**Stage 3 engine:** `AlgorithmicSignalEngine.generate_hypothesis()`
**Stage 6 (AI Validator):** ⏭️ Skipped entirely

### Stage 3 — Signal Rule Sources

The engine reads the rule list from `params["signal_rules"]` — each rule is one of these six sources:

| Source | What it detects |
|---|---|
| `ema_crossover` | Fast EMA crosses slow EMA (RSI must not be extreme) |
| `macd_crossover` | MACD histogram crosses zero |
| `rsi_reversal` | RSI crosses oversold (30) or overbought (70) threshold |
| `bollinger_breakout` | Price breaks Bollinger Band %B threshold |
| `adx_trend` | ADX above threshold AND +DI/-DI dominance |
| `bos_confirm` | Break of Structure — price clears a prior swing high/low |

Rules are combined with `params["signal_logic"]`:
- **AND** — all active rules must agree
- **OR** — any one rule fires
- **MAJORITY** — more than half agree

**Confidence formula:**
```
agreement_ratio = rules_agreeing / total_active_rules
rsi_factor      = min(|rsi - 50| / 50, 1.0) × 0.08
confidence      = min(0.55 + (agreement_ratio × 0.25) + rsi_factor, 0.90)
```

**Output:** `DirectionHypothesis(direction=BUY, confidence=0.72, setup_tag="pullback")`
No AI. No price levels. Pure math on M15 OHLCV indicators.

### Full cycle path
```
MarketGate → Regime → AlgoEngine (rules + indicators) → TradeConstructor
→ Invalidation → Quality → Conviction → [AI skipped] → RiskGate → ExecGuard → TRADE
```

---

## Mode 2 — `algo_ai`

**Stage 3 engine:** Same `AlgorithmicSignalEngine` as algo_only
**Stage 6 (AI Validator):** ✅ Runs — bounded review only

### Stage 3

Identical to `algo_only`. Rules + indicators → direction + confidence. No AI involvement in the hypothesis.

### Stage 6 — Bounded AI Validator

After the conviction scorer decides "TRADE", the AI validator gets a second look.

**What the AI *can* do:**
- Approve the signal as-is
- Reject it entirely
- Nudge confidence up by a max of +0.05

**What the AI *cannot* do (enforced in code):**
- Suggest a different entry price
- Adjust the stop-loss
- Adjust the take-profit
- Change the direction

If the AI response contains any of those fields, they are silently ignored and logged:
```
"AI attempted price-level adjustment — ignored"
```

**Use case:** You trust the algo to find direction but want a sanity-check from an AI
model before committing real money.

### Full cycle path
```
MarketGate → Regime → AlgoEngine (same as algo_only) → TradeConstructor
→ Invalidation → Quality → Conviction → BoundedAIValidator → RiskGate → ExecGuard → TRADE
```

---

## Mode 3 — `ai_signal`

**Stage 3 engine:** `MultiAssetSignalEngine.generate_hypothesis()` (LLM call)
**Stage 6 (AI Validator):** ✅ Runs — same bounded validator

### Stage 3 — The AI Prompt

The AI receives a structured market brief in five tiers:

| Tier | Contents |
|---|---|
| **T1** Market State | Regime, macro regime, session, current price |
| **T2** Primary Structure | M15 BOS, FVG locations, swing structure, confluence scores |
| **T3** Trend Context | H1 EMA200, M15 EMAs, RSI, ADX, MACD, Bollinger |
| **T4** Macro Context | DXY bias, sentiment |
| **T5** Tool Readings | Optional tool confirmations |

The system prompt explicitly instructs the model:
- Output **direction only** (BUY / SELL / NEUTRAL)
- Output a **confidence score** (0–1) and **setup tag**
- **Do not output SL, TP, or entry zone** — those are forbidden fields

Before the prompt is sent, a confluence score is computed internally:
```
Bull/Bear factors: H1 trend, M15 trend, price vs EMA200,
                   BOS direction, FVG presence, session score
```
This confluence context is embedded in the prompt so the AI receives structured
context, not raw OHLCV numbers.

**Response parsing** does three passes (direct JSON → markdown extraction → repair)
and silently drops any `stop_loss`, `take_profit`, or `entry_zone` the model returns.

**Output:** `DirectionHypothesis(direction=BUY, confidence=0.81, setup_tag="continuation", reasoning="...")`

### Stage 6 — Second AI Call

The bounded validator runs again. This means `ai_signal` mode makes **two AI calls per cycle**:
1. Stage 3: AI generates direction hypothesis
2. Stage 6: AI validates the constructed signal (approve / reject / ±0.05 confidence)

Conviction scoring in `ai_signal` mode also uses the AI's original confidence as an
input alongside structural quality scores.

### Full cycle path
```
MarketGate → Regime → MultiAssetSignalEngine (LLM call #1)
→ TradeConstructor (structure SL/TP — same as other modes)
→ Invalidation → Quality → Conviction (uses AI confidence)
→ BoundedAIValidator (LLM call #2) → RiskGate → ExecGuard → TRADE
```

---

## Side-by-side comparison

| | `algo_only` | `algo_ai` | `ai_signal` |
|---|---|---|---|
| **Stage 3 engine** | Rules + indicators | Rules + indicators | LLM (full market brief) |
| **Direction source** | Math | Math | Language model |
| **Indicators used** | EMA, MACD, RSI, BB, ADX, BOS | Same | Same (as context for AI) |
| **AI calls per cycle** | 0 | 1 (Stage 6) | 2 (Stage 3 + Stage 6) |
| **SL/TP source** | TradeConstructor (structure) | Same | Same |
| **Confidence source** | Algo formula | Algo formula | AI output |
| **Stage 6** | Skipped | Bounded review | Bounded review |
| **Latency** | Fastest | Medium | Slowest |
| **Deterministic?** | Yes, fully | Mostly | Partially (LLM) |

---

## What is identical in all three modes

Everything from Stage 3B onward shares the exact same code:

- **TradeConstructor** — SL from swing low/high or FVG bottom/top. TP from `SL distance × tp1_rr / tp2_rr`. Bounds checked. No ATR fallback. If no valid structure exists, no trade is emitted.
- **Structural Invalidation** — hard checks: SL direction, minimum R:R, distance bounds, regime compatibility
- **Risk Gate** — position sizing, portfolio correlation, equity curve gate
- **Execution Guard** — tick jump detection, deduplication, DELAY / EXECUTE / BLOCK decision

The mode only determines *how* direction + confidence arrive at Stage 3B. Everything
that builds and validates the actual trade is mode-agnostic.

---

## Key files

| File | Relevant function | Purpose |
|---|---|---|
| `trading/loop.py` | `_cycle_v4()` | Main v4 entry point; selects mode |
| `trading/loop.py` | `_build_v4_orchestrator()` | Builds orchestrator with TradeConstructor |
| `pipeline/orchestrator.py` | `PipelineOrchestrator.run()` | 8-stage coordinator |
| `signals/algorithmic.py` | `AlgorithmicSignalEngine.generate_hypothesis()` | algo_only / algo_ai Stage 3 |
| `signals/algorithmic.py` | `compute_direction()` | Pure sync rule dispatcher (also used by backtester) |
| `signals/engine.py` | `MultiAssetSignalEngine.generate_hypothesis()` | ai_signal Stage 3 |
| `pipeline/construction.py` | `TradeConstructor.construct()` | Stage 3B — structure SL/TP derivation |
| `pipeline/invalidation.py` | `StructuralInvalidator.validate()` | Stage 4A hard safety-net |
| `pipeline/ai_validator.py` | `BoundedAIValidator.validate()` | Stage 6 bounded AI review |
| `core/normalization.py` | `normalize_distance()` / `check_bounds()` | Shared price distance math |
