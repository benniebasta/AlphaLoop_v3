# AlphaLoop v3 — Full System Architecture

> Generated: 2026-04-09

---

## THE 3 SIGNAL MODES

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SIGNAL MODE ROUTING                                │
├─────────────────┬────────────────────────┬──────────────────────────────────┤
│   algo_only     │      algo_ai           │         ai_signal                │
├─────────────────┼────────────────────────┼──────────────────────────────────┤
│ Pure rules:     │ Algo signal + AI layer │ AI generates the signal itself   │
│ EMA crossover   │                        │                                  │
│ MACD, RSI, ADX  │ Same algo stages 1-5   │ Stages 1-8 (full)                │
│ BOS, Bollinger  │ +Stage 6: BoundedAI    │ AI produces DirectionHypothesis  │
│                 │  Validator             │ → TradeConstructor derives SL/TP │
│ Stages 1-5 only │                        │ AI blends into conviction score  │
│ No AI           │ AI can: ±0.05 conf     │                                  │
│                 │ AI cannot: change      │ Structural safety net still runs │
│                 │ direction, SL, R:R     │ (invalidation, quality, gates)   │
└─────────────────┴────────────────────────┴──────────────────────────────────┘
```

---

## THE 8-STAGE PIPELINE

```
  Market Data (live OHLCV + indicators, M1→D1)
         │
         ▼
 ┌───────────────┐
 │  Stage 1      │  MarketGate            pipeline/market_gate.py
 │  HARD GATE    │  ─ Data freshness (< 300s stale)
 │               │  ─ Min bars (≥ 200)
 │               │  ─ Spread sanity (≤ 3x median)
 │               │  ─ Kill switch
 └──────┬────────┘
  BLOCKED → exit     PASS → continue
         │
         ▼
 ┌───────────────┐
 │  Stage 2      │  RegimeClassifier      pipeline/regime.py
 │  PARAMETERISE │  ─ Regime: trending/ranging/volatile/neutral
 │               │  ─ Macro: risk_on/risk_off/neutral
 │               │  ─ Volatility: compressed/normal/elevated/extreme
 │               │  ─ Sets: allowed_setups, confidence_ceiling,
 │               │          min_entry_adjustment, size_multiplier,
 │               │          weight_overrides
 │               │  NEVER BLOCKS — only parameterises downstream
 └──────┬────────┘
         │
         ▼
 ┌───────────────┐
 │  Stage 3      │  Signal Generation
 │  HYPOTHESIS   │  algo → AlgorithmicSignalEngine (EMA/MACD/RSI rules)
 │               │  ai   → MultiAssetSignalEngine (GPT prompt tiers T1-T5)
 │               │  Output: DirectionHypothesis (BUY/SELL + confidence only)
 │               │          NO stop-loss, NO take-profit yet
 └──────┬────────┘
  NO_SIGNAL → exit    hypothesis → continue
         │
         ▼
 ┌───────────────┐
 │  Stage 3B     │  TradeConstructor      pipeline/construction.py
 │  CONSTRUCTION │  ─ Derives SL from structure (swing low/high → FVG → ATR)
 │               │  ─ Derives entry zone from bid/ask midpoint ± 0.25×ATR
 │               │  ─ Derives TP1/TP2 from SL distance × target R:R
 │               │  Output: CandidateSignal (full trade, no SL guessing)
 └──────┬────────┘
         │
         ▼
 ┌───────────────┐
 │  Stage 4A     │  StructuralInvalidator  pipeline/invalidation.py
 │  SAFETY NET   │  ─ Universal: SL direction, R:R, SL distance bounds
 │               │  ─ Setup-specific matrix (breakout needs BOS+EMA200 etc.)
 │               │  HARD_INVALIDATE → REJECTED (blocks)
 │               │  SOFT_INVALIDATE → conviction penalty (passes)
 └──────┬────────┘
  HARD → exit     SOFT/PASS → continue (with penalty)
         │
         ▼
 ┌───────────────┐
 │  Stage 4B     │  StructuralQuality     pipeline/quality.py
 │  SOFT SCORING │  ─ Runs all tools in extract_features() mode
 │               │  ─ Groups: trend, momentum, structure, volume, volatility
 │               │  ─ GroupScorer → ConfidenceEngine → overall_score
 │               │  Output: QualityResult (tool_scores, group_scores, max_score)
 │               │  NEVER BLOCKS — low scores reduce conviction below
 └──────┬────────┘
         │
         ▼
 ┌───────────────┐
 │  Stage 5      │  ConvictionScorer      pipeline/conviction.py
 │  DECISION     │  ─ weighted sum of group scores → raw conviction
 │               │  ─ if ai_signal: blend AI confidence in
 │               │  ─ deduct penalties (invalidation, conflict, portfolio)
 │               │  ─ apply regime ceiling
 │               │  ─ quality floor checks:
 │               │     • no tool scored > 60 → HOLD
 │               │     • overall < 35 → HOLD
 │               │     • 3+ tools < 25 → HOLD
 │               │  ─ decision:
 │               │     conviction ≥ strong_entry(75) → TRADE 1.0x
 │               │     conviction ≥ min_entry(60+adj) → TRADE 0.6x
 │               │     otherwise → HOLD
 └──────┬────────┘
  HOLD → exit     TRADE → continue
         │
         ▼
 ┌───────────────┐
 │  Stage 6      │  BoundedAIValidator    pipeline/ai_validator.py
 │  AI VALIDATE  │  (algo_ai / ai_signal only)
 │               │  ─ AI can approve, reduce conf, boost ±0.05, tweak TP
 │               │  ─ AI cannot: change direction, touch SL, break R:R
 │               │  ─ Post-adjustment: re-runs invalidation checks
 └──────┬────────┘
         │
         ▼
 ┌───────────────┐
 │  Stage 7      │  RiskGate              pipeline/risk_gate.py
 │  CAPACITY     │  ─ Daily loss / session loss limits
 │               │  ─ Drawdown pause guard
 │               │  ─ Portfolio cap (correlation-adjusted)
 │               │  ─ Equity curve scaler (drawdown → size reduction)
 └──────┬────────┘
  blocked → exit    allowed → continue (with size_modifier)
         │
         ▼
 ┌───────────────┐
 │  Stage 8      │  ExecutionGuard        pipeline/execution_guard.py
 │  LAST MILE    │  ─ Signal hash dedup (same setup seen recently?)
 │               │  ─ Near-position dedup (open trade within 1 ATR?)
 │               │  ─ Confidence variance (AI output unstable?)
 │               │  ─ Spread spike / tick jump / liquidity vacuum
 │               │    → DELAY (queued, retried next cycle)
 └──────┬────────┘
  BLOCK → exit    DELAY → queue    EXECUTE → continue
         │
         ▼
 ┌───────────────┐
 │  SIZING       │  Combined scalar:
 │               │  conviction(0.6|1.0) × regime(0.6-1.1)
 │               │  × freshness(0-1) × risk_gate × equity_curve
 └──────┬────────┘
         │
         ▼
  ExecutionOrchestrator       trading/execution_orchestrator.py
         │
         ▼
  ExecutionService → MT5Executor → FILLED
         │
         ├─ Persist to DB
         ├─ Register on RiskMonitor
         └─ Publish TradeOpened event → Telegram
```

---

## REPOSITIONER (TRADE MANAGEMENT, EVERY CYCLE)

```
On each trading cycle, for every open position:

TradeRepositioner.check(open_trade, context)    risk/repositioner.py
    │
    ├─ Opposite signal from pipeline? ──────────────────► full_close
    │
    ├─ News risk in next 0-15 min?
    │   └─ HIGH / CRITICAL ──────────────────────────────► tighten_sl OR partial_close (50%)
    │
    ├─ Volume spike? (ratio ≥ 2.5x) ────────────────────► tighten_sl (move to break-even)
    │
    └─ Volatility spike? (ATR ≥ 1.8x baseline) ─────────► tighten_sl

All actions emit RepositionEvent with old_sl / new_sl / lots_closed
```

---

## META-LOOP: STRATEGY EVOLUTION (BACKGROUND)

```
TradingLoop
    │
    └── on every TradeClose event ──► MetaLoop.on_trade_closed()   trading/meta_loop.py
                                            │
                                            ├─ Record R-multiple
                                            ├─ Feed RollbackTracker (if monitoring)
                                            │
                                            │  (every N=20 trades)
                                            ▼
                                    ┌──────────────────────┐
                                    │  ResearchAnalyzer    │  research/analyzer.py
                                    │  ─ Sharpe, win rate  │
                                    │  ─ Profit factor     │
                                    │  ─ Setup breakdown   │
                                    │  ─ R-multiple array  │
                                    └──────────┬───────────┘
                                               │ degraded?
                                               ▼
                                    ┌──────────────────────┐
                                    │  AutoImprover (AI)   │
                                    │  suggest_params()    │
                                    │  ─ AI reads report   │
                                    │  ─ Suggests new      │
                                    │    strategy params   │
                                    └──────────┬───────────┘
                                               │
                                    ┌──────────▼───────────┐
                                    │  Statistical Gates   │
                                    │  ─ Welch's t-test    │
                                    │    p < 0.05 required │
                                    │  ─ Walk-forward OOS  │
                                    │    60d IS / 30d OOS  │
                                    │    OOS Sharpe > 0    │
                                    │    max_dd < 20%      │
                                    └──────────┬───────────┘
                                               │ passed
                                               ▼
                                    Create new strategy version
                                    (source="autolearn")
                                               │
                                    ┌──────────▼───────────┐
                                    │  RollbackTracker     │
                                    │  Monitor next 30     │
                                    │  trades              │
                                    │  ─ new_sharpe <      │
                                    │    prev × 0.70?      │
                                    │    → auto rollback   │
                                    └──────────────────────┘

  Degradation thresholds are REGIME-AWARE:
    trending  → 0.65 (lenient, volatile conditions)
    ranging   → 0.75 (strict, tight edges)
    volatile  → 0.55 (most lenient)
    neutral   → 0.70
    dead mkt  → 0.80 (most strict)

  Cooldown: 24h minimum between cycles
```

---

## SCORING INTERNALS

```
Tool Results (extract_features mode)
    │
    ▼
FeatureAggregator  →  group each tool by its group label
    │
    ▼
GroupScorer (per group: trend, momentum, structure, volume, volatility)
    │  ─ simple mean (no tracker)
    │  ─ OR win-rate weighted mean (with ToolPerformanceTracker)
    │     tools that historically underperform contribute less
    ▼
Group Scores {group → 0-100}
    │
    ▼
ConfidenceEngine  →  Σ(group_weight × group_score)  =  raw conviction
    │
    ▼
ConvictionScorer
    ├─ − invalidation penalty (SOFT_INVALIDATE hits)
    ├─ − conflict penalty (group score spread > 40)
    ├─ − portfolio penalty (macro exposure, low budget)
    ├─ min(result, regime.confidence_ceiling)
    ├─ quality floor enforcement (vetoes HOLD before threshold check)
    └─ → final conviction score + TRADE/HOLD decision
```

---

## FULL LOOP SUMMARY (ONE CYCLE)

```
TradingLoop._execute_cycle()    trading/loop.py

  1. Kill switch / risk limit check
  2. Build MarketContext (OHLCV, indicators M1→D1, live price/spread)
  3. Load active strategy (hot-reload if fingerprint changed)
  4. Route to signal mode handler
  5. PipelineOrchestrator.run() → PipelineResult
  6. Check delayed signals from ExecutionGuard queue
  7. ExecutionOrchestrator.execute() → MT5 order (if TRADE)
  8. Publish events (CycleCompleted, PipelineStep*, etc.)
  9. TradeRepositioner.check() → manage open positions
 10. MetaLoop.on_trade_closed() → strategy evolution (background)
```

---

## CLASS / FILE MAP

| Component             | File                                    | Class                    | Key Methods                              |
|-----------------------|-----------------------------------------|--------------------------|------------------------------------------|
| Trading Loop          | `trading/loop.py`                       | `TradingLoop`            | `_execute_cycle()`                       |
| Pipeline              | `pipeline/orchestrator.py`              | `PipelineOrchestrator`   | `run()`, `check_delayed()`               |
| Market Gate           | `pipeline/market_gate.py`               | `MarketGate`             | `check()`                                |
| Regime                | `pipeline/regime.py`                    | `RegimeClassifier`       | `classify()`                             |
| Signal Gen (Algo)     | `signals/algorithmic.py`               | `AlgorithmicSignalEngine`| `generate_hypothesis()`, `compute_direction()` |
| Signal Gen (AI)       | `signals/engine.py`                    | `MultiAssetSignalEngine` | `generate_signal()`, `generate_hypothesis()` |
| Construction          | `pipeline/construction.py`              | `TradeConstructor`       | `construct()`                            |
| Invalidation          | `pipeline/invalidation.py`              | `StructuralInvalidator`  | `validate()`                             |
| Quality               | `pipeline/quality.py`                   | `StructuralQuality`      | `evaluate()`                             |
| Conviction            | `pipeline/conviction.py`                | `ConvictionScorer`       | `score()`                                |
| AI Validator          | `pipeline/ai_validator.py`              | `BoundedAIValidator`     | `validate()`                             |
| Risk Gate             | `pipeline/risk_gate.py`                 | `RiskGateRunner`         | `check()`                                |
| Execution Guard       | `pipeline/execution_guard.py`           | `ExecutionGuardRunner`   | `check()`, `tick_delay()`, `queue_delay()` |
| Repositioner          | `risk/repositioner.py`                  | `TradeRepositioner`      | `check()`                                |
| Meta-Loop             | `trading/meta_loop.py`                  | `MetaLoop`               | `on_trade_closed()`, `_run_cycle()`      |
| Researcher            | `research/analyzer.py`                  | `ResearchAnalyzer`       | `run()`, `check_retraining_needed()`, `suggest_params()` |
| Execution Orchestrator| `trading/execution_orchestrator.py`     | `ExecutionOrchestrator`  | `execute()`, `update_state()`            |
| Execution Service     | `execution/service.py`                  | `ExecutionService`       | `execute_market_order()`                 |
| Runtime Utilities     | `trading/runtime_utils.py`              | (module-level functions) | `safe_json_payload()`, `current_account_balance()`, `current_runtime_strategy()`, `current_strategy_reference()` |
| Confidence Engine     | `scoring/confidence_engine.py`          | `ConfidenceEngine`       | `compute()`                              |
| Group Scorer          | `scoring/group_scorer.py`               | `GroupScorer`            | `score_group()`, `score_all_groups()`    |
| Tool Tracker          | `scoring/tool_tracker.py`               | `ToolPerformanceTracker` | `record()`, `win_rate()`                 |

---

## SIZING SCALAR CHAIN

```
conviction_scalar   (0.0 | 0.6 | 1.0)     ← conviction decision
      ×
regime_scalar       (0.6 – 1.1)            ← regime.size_multiplier
      ×
freshness_scalar    (0.0 – 1.0)            ← decays with candles elapsed
      ×
risk_gate_scalar    (0.0 – 1.0)            ← risk guards
      ×
equity_curve_scalar (0.0 – 1.0)            ← drawdown protection
      =
final_lot_multiplier
```

---

## KEY REGIME PARAMETERS

| Regime   | ceiling | min_entry_adj | size_mult | degradation_threshold |
|----------|---------|---------------|-----------|----------------------|
| trending | 95      | −5            | 1.1       | 0.65                 |
| ranging  | 80      | +5            | 0.8       | 0.75                 |
| volatile | 85      | +10           | 0.6       | 0.55                 |
| neutral  | 90      | 0             | 1.0       | 0.70                 |

---

## QUALITY FLOOR CONSTANTS  (`pipeline/conviction.py`)

| Floor                    | Value | Meaning                                      |
|--------------------------|-------|----------------------------------------------|
| `OVERALL`                | 35.0  | Overall structural score must exceed this    |
| `CONTRADICTION_COUNT`    | 3     | At most 2 tools may score below 25           |
| `CONTRADICTION_THRESHOLD`| 25.0  | Per-tool low-score threshold                 |
| `MAX_SCORE_MIN`          | 60.0  | At least one tool must score above this      |
