# Pipeline Stages — All 3 Signal Modes

Full stage-by-stage breakdown of `PipelineOrchestrator.run()` for `algo_only`, `algo_ai`, and `ai_signal` modes.

Source: `src/alphaloop/pipeline/orchestrator.py`

---

## Stage 1 — MarketGate

**Same for all 3 modes.**

Checks: spread ratio, data quality, bars available. Hard gate — if it fails, outcome = `REJECTED` immediately, nothing else runs.

---

## Stage 2 — RegimeClassifier

**Same for all 3 modes. Never blocks.**

Classifies the market as trending/ranging/volatile/neutral, sets `allowed_setups`, `size_multiplier`, `weight_overrides`. Output flows through every downstream stage but doesn't block by itself. If your `setup_type` later doesn't match `allowed_setups`, you get `HELD` after Stage 3B.

---

## Stage 3 — Hypothesis Tools + Signal Generator

**First mode split.**

**Before** the signal generator is called, `hypothesis_tools` run and attach their results to `context.tool_results`. These are indicator tools (ema_crossover, macd_filter, rsi_feature, fast_fingers) that the AI reads as the **T5 tier** of its prompt. They run regardless of mode — their results are ignored by the algo engine, but the AI engine reads them.

Then:

| Mode | Engine | What it does |
|---|---|---|
| `algo_only` / `algo_ai` | `AlgorithmicSignalEngine` | Reads M15 indicators directly. Applies signal rules (EMA crossover, RSI, MACD, ADX) with OR/AND/MAJORITY logic. Computes `confidence = 0.55–0.90` from agreement ratio. Returns `DirectionHypothesis`. |
| `ai_signal` | `MultiAssetSignalEngine` | Builds a 5-tier market brief. Sends to LLM. Gets back `{direction, confidence, setup_tag, reasoning}`. Returns `DirectionHypothesis` with `confidence = 0.0–1.0`. |

**AI prompt tiers (ai_signal only):**

| Tier | Contents |
|---|---|
| T1 | Regime, macro regime, session, current price |
| T2 | M15 BOS, FVG locations, swing structure, confluence scores |
| T3 | H1 EMA200, M15 EMAs, RSI, ADX, MACD, Bollinger |
| T4 | DXY bias, sentiment, upcoming news |
| T5 | Tool readings from hypothesis_tools |

If the generator returns `None` → `NO_SIGNAL`, pipeline stops.

---

## Stage 3B — Trade Construction

**Same for all 3 modes.**

Takes the `DirectionHypothesis` (direction + confidence only, no prices). `TradeConstructor` derives SL/TP from market structure:

- SL priority: swing lows/highs → FVG bottom/top → ATR×1.5 fallback
- TP: `SL distance × tp1_rr` / `tp2_rr`

Returns a `CandidateSignal` with full SL/TP. If no valid SL can be constructed → `NO_CONSTRUCTION`, pipeline stops.

After construction, `bos_guard`, `fvg_guard`, `swing_structure` plugins run as **warnings only** — they log disagreement but do not block.

Setup type is checked against regime's `allowed_setups`. Mismatch → `HELD`.

---

## Stage 4A — Structural Invalidation

**Same code for all modes — `signal_mode` controls which checks run.**

Universal checks always run (all modes):
- SL direction
- R:R ratio (hard min 1.0, soft min 1.5)
- SL distance bounds
- Confidence floor (hard min 0.30)
- Regime setup compatibility

Strategy-type checks from invalidation matrix — skipped per mode:

| Check | `algo_only` / `algo_ai` | `ai_signal` |
|---|---|---|
| `bos_required` | Runs if `bos_guard=True` | **Skipped** — AI processed T2 |
| `ema200_alignment` | Runs if `ema200_filter=True` | **Skipped** — AI processed T3 |
| `swing_alignment` | Runs if `swing_structure=True` | **Skipped** — AI processed T2 |
| `bollinger_position` | Runs if `bollinger_filter=True` | **Skipped** — AI processed T3 |

Execution/safety checks always run regardless of mode:
`tick_jump_guard`, `liq_vacuum_guard`, `vwap_guard`, `session_filter`, `volatility_filter`, `news_filter`, `risk_filter`, `correlation_guard`, `fast_fingers`

Hard failure → `REJECTED`. Soft failure → conviction penalty, continues.

Source: `src/alphaloop/pipeline/invalidation.py`

---

## Stage 4B — Structural Quality

**Same for all 3 modes. Never blocks.**

Scores the market context across quality groups. Output feeds into Stage 5 conviction blending. Regime can override the weights.

---

## Stage 5 — Conviction Scorer

**Second mode split.**

| Mode | How conviction is computed |
|---|---|
| `algo_only` / `algo_ai` | Pure structural quality scores + invalidation penalty |
| `ai_signal` | `(AI confidence × ai_weight) + (structural quality × (1 − ai_weight))` |

In `ai_signal` mode, `signal.raw_confidence` (from the LLM) is passed as `raw_confidence` to the scorer and blended against the structural quality score via `ai_weight`. The AI's number directly inflates or deflates the final conviction before the TRADE/HOLD decision.

HOLD → pipeline stops.

Source: `src/alphaloop/pipeline/conviction.py`

---

## Stage 6 — AI Validator

**Third mode split.**

| Mode | What happens |
|---|---|
| `algo_only` | Skipped entirely |
| `algo_ai` | Runs. `validated is None` → hard `REJECTED`. Can adjust confidence ±0.05 max. |
| `ai_signal` | Runs same code. `validated is None` → hard `REJECTED`. Same behaviour. |

The AI validator receives: signal, regime, quality, conviction, context. It can:
- Approve → signal proceeds (possibly with small confidence nudge)
- Reject → returns `None` → `REJECTED` outcome

The validator cannot change SL, TP, entry zone, or direction — constraint-first policy.

Source: `src/alphaloop/pipeline/ai_validator.py`

---

## Stage 7 — Risk Gate

**Same for all 3 modes.**

Portfolio-level checks: equity curve gate, risk utilisation, position correlation. Produces `size_modifier` and `equity_curve_scalar` that flow into final sizing.

Failure → `REJECTED`.

---

## Stage 8 — Execution Guard

**Same for all 3 modes.**

Tick-jump detection, deduplication, spread spike check.

| Action | Result |
|---|---|
| `EXECUTE` | Continues to sizing |
| `DELAY` | Signal queued, re-evaluated next candle with freshness decay |
| `BLOCK` | `REJECTED` |

---

## Sizing

**Same for all 3 modes.**

```
final_size = conviction_scalar
           × regime_scalar
           × freshness_scalar
           × risk_gate_scalar
           × equity_curve_scalar
```

Freshness decays if signal was delayed. If freshness = 0 → `HELD`.

---

## Shadow Mode

All stages run fully. Signal is constructed and logged but **not executed**. Outcome = `HELD`.

---

## What actually differs between modes

Only 3 things in the actual code:

| | `algo_only` | `algo_ai` | `ai_signal` |
|---|---|---|---|
| **Stage 3 engine** | AlgorithmicSignalEngine | AlgorithmicSignalEngine | MultiAssetSignalEngine (LLM) |
| **Stage 4A structural checks** | All enabled checks run | All enabled checks run | Direction/structural checks skipped; safety checks still run |
| **Stage 5 conviction** | Structural quality only | Structural quality only | Blended with AI confidence via `ai_weight` |
| **Stage 6 AI Validator** | Skipped | Runs — hard veto | Runs — hard veto |
| **AI calls per cycle** | 0 | 1 (Stage 6) | 2 (Stage 3 + Stage 6) |

---

## Possible outcomes

| Outcome | Meaning |
|---|---|
| `NO_SIGNAL` | Signal generator returned None |
| `NO_CONSTRUCTION` | TradeConstructor could not derive valid SL/TP |
| `HELD` | Conviction HOLD, regime mismatch, freshness=0, or shadow mode |
| `DELAYED` | Execution guard queued signal for next candle |
| `REJECTED` | Hard block at any stage (MarketGate, invalidation, AI validator, risk gate, exec guard) |
| `TRADE_OPENED` | All stages passed, trade sent to execution |

---

## Key files

| File | Stage |
|---|---|
| `pipeline/orchestrator.py` | Full 8-stage coordinator |
| `pipeline/market_gate.py` | Stage 1 |
| `pipeline/regime.py` | Stage 2 |
| `signals/algorithmic.py` | Stage 3 — algo_only / algo_ai engine |
| `signals/engine.py` | Stage 3 — ai_signal engine (MultiAssetSignalEngine) |
| `pipeline/construction.py` | Stage 3B — TradeConstructor |
| `pipeline/invalidation.py` | Stage 4A — StructuralInvalidator |
| `pipeline/quality.py` | Stage 4B — StructuralQuality |
| `pipeline/conviction.py` | Stage 5 — ConvictionScorer |
| `pipeline/ai_validator.py` | Stage 6 — BoundedAIValidator |
| `pipeline/risk_gate.py` | Stage 7 — RiskGateRunner |
| `pipeline/execution_guard.py` | Stage 8 — ExecutionGuardRunner |
| `pipeline/freshness.py` | Sizing — freshness decay |
