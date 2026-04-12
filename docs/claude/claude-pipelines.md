# AlphaLoop v3 — Pipeline Flows

## Purpose
Step-by-step flow for each major pipeline: live trading, backtesting, SeedLab discovery, and deployment promotion.

---

## 1. Live Trading Pipeline

**Entry:** `TradingLoop.run_cycle()` in `src/alphaloop/trading/loop.py`

```
┌─ Step 0: Cycle Announced ────────────────────────────────────┐
│  → CycleStarted(symbol, instance_id, cycle) published        │
│  → Always fires — even if subsequent guards block the cycle  │
│  → Activates the 🔄 Cycle tile in the Raw Signal Log modal   │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 1: Risk Check ──────────────────────────────────────────┐
│  CrossInstanceRiskAggregator.can_open_trade()                 │
│  → If blocked: PipelineBlocked(blocked_by="cross_instance_risk") │
│  RiskMonitor.can_open_trade()                                 │
│  → If blocked: PipelineBlocked(blocked_by="risk_monitor")    │
│  CircuitBreaker.is_open                                       │
│  → If open:   PipelineBlocked(blocked_by="circuit_breaker")  │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 2: Load Strategy ──────────────────────────────────────┐
│  strategy_loader.load_active_strategy(symbol)                 │
│  → ActiveStrategyConfig: params, tool config, AI overrides    │
│  overlay_loader.load_overlay_config(symbol)                   │
│  → DryRunOverlayConfig: per-card tool overrides               │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 3: Market Context (_build_context) ────────────────────┐
│  MT5 sync calls (main thread, <50ms — thread-safe):           │
│  ├── mt5.copy_rates_from_pos(H1, 50) → ATR(14), EMA(21/55)  │
│  ├── mt5.copy_rates_from_pos(M15, 100) → RSI(14)             │
│  ├── Symbol auto-resolved (XAUUSD → XAUUSDm on Exness)       │
│  └── MT5 connects even in dry-run mode (needed for data)      │
│  Returns AttrDict — supports both context.session (tools)     │
│  and context.get("timeframes") (algo engine)                  │
│  Includes: session(+is_weekend), price, indicators, risk_mon  │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 4: Filter Pipeline ────────────────────────────────────┐
│  FilterPipeline.run(context)                                  │
│  → session_filter → news_filter → volatility_filter →        │
│    dxy_filter → sentiment_filter → risk_filter               │
│  → Short-circuit on first block                               │
│  → Check: not pipeline_result.get("allow_trade", True)        │
│  → If blocked: PipelineBlocked event → next cycle             │
│  → Event bridged to web server via HTTP POST /api/events/ingest│
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 5: Signal Generation ──────────────────────────────────┐
│  Branch by signal_mode (per-strategy, set on Strategy Card):  │
│  Three modes:                                                  │
│  ├── "algo_only":  AlgorithmicSignalEngine → deterministic    │
│  │                 direction hypothesis; no AI cost            │
│  ├── "algo_ai":    AlgorithmicSignalEngine → FeaturePipeline  │
│  │                 (22 scoring tools) → conviction blend       │
│  └── "ai_signal":  SignalEngine LLM query → DirectionHypo-    │
│                    thesis from AI; AI validator required       │
│  → All modes: TradeConstructor (constraint-first SL/TP)       │
│  → SignalGenerated event                                       │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 6: v4 Pipeline (pipeline/orchestrator.py) ─────────────┐
│  Stage 4A: StructuralInvalidator — hard blocks only           │
│            (SL/TP ordering, R:R, SL distance, setup type)     │
│  Stage 4B: StructuralQuality — soft scoring                   │
│  Stage 5:  ConvictionScorer — HOLD / TRADE decision           │
│  Stage 6:  BoundedAIValidator (conditional)                   │
│            → Required for algo_ai and ai_signal modes         │
│            → Skipped for algo_only                            │
│            → Bounded: confidence adjust only (±0.05 max)      │
│            → Live mode: AI error → REJECT (fail-closed)       │
│  Stage 7:  RiskGate — daily loss, kill switch, concurrent cap │
│  Stage 8:  ExecutionGuard — tick jump, volatility spike       │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 7: Risk Guards ────────────────────────────────────────┐
│  7 stateful guards check in sequence:                         │
│  SignalHashFilter → ConfidenceVarianceFilter →                │
│  SpreadRegimeFilter → EquityCurveScaler →                    │
│  DrawdownPauseGuard → NearDedupGuard → PortfolioCapGuard     │
│  → If blocked: PipelineBlocked event → next cycle             │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 8: Position Sizing ────────────────────────────────────┐
│  PositionSizer.calculate(signal, context, account_info)       │
│  → ATR-based lot size × confidence multiplier                 │
│  → Margin cap enforcement (max 20%)                           │
│  → Macro modifier + vol regime adjustment                     │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 9: Execution ──────────────────────────────────────────┐
│  if dry_run: log signal → done                                │
│  else: MT5Executor.execute(order) via asyncio.to_thread       │
│  → OrderResult → TradeOpened event                            │
│  → Save to TradeLog via TradeRepo                             │
│  → Telegram notification                                      │
└───────────────────────────────────────────────────────────────┘
```

---

## 2. Backtest Pipeline

**Entry:** `POST /api/backtests` → `backtester/runner.py:start_backtest()`

```
Step 1: Create DB record (state="pending")
Step 2: Spawn asyncio.Task
        │
        v
Step 3: Fetch OHLCV data
  ├── MT5: asyncio.to_thread(mt5.copy_rates_from_pos)
  └── yfinance fallback (auto-capped by timeframe)
  → Compute data_hash for checkpoint matching
        │
        v
Step 4: Check for checkpoint
  ├── If checkpoint exists AND data_hash matches:
  │   Resume from saved generation + best params
  └── Else: start fresh
        │
        v
Step 5: Generation 1 — Baseline
  → asyncio.to_thread(asyncio.run(engine.run(default_params)))
  → Log: Sharpe, WR, PnL
        │
        v
Step 6: Generation 2+ — Optuna TPE
  ├── 30 trials per generation
  ├── Each trial: mutate 7 params on 80% train split
  │   → asyncio.to_thread(asyncio.run(engine.run(trial_params)))
  ├── Validate best on 20% holdout
  ├── Overfit check: train-val Sharpe gap > 0.30 → reject
  └── Early stop: 2 gens no improvement
        │
        v
Step 7: Save results
  ├── Update DB: state="completed", best params, metrics
  ├── Save checkpoint for resume
  └── Auto-create strategy version JSON
```

---

## 3. SeedLab Discovery Pipeline

**Entry:** `POST /api/seedlab` → `seedlab/background_runner.py`

```
Step 1: Generate Seeds
  ├── Template seeds (10 predefined strategies)
  └── Optional combinatorial seeds (up to 30)
        │
        v
Step 2: Detect Market Regimes
  → regime_detector.py: trending/ranging/volatile/quiet
        │
        v
Step 3: Multi-Regime Backtest
  → For each seed × each regime:
     regime_runner.py runs backtest on regime-specific data
        │
        v
Step 4: Extract Metrics
  → SeedMetrics per seed (Sharpe, WR, DD, per-regime breakdown)
        │
        v
Step 5: Stability Analysis
  → stability.py: cross-regime consistency check
  → Reject seeds that only work in one regime
        │
        v
Step 6: Score & Rank
  → ranking.py: composite score (profit + stability + risk)
  → Sort by score, top N proceed
        │
        v
Step 7: Build Strategy Cards
  → strategy_card.py: immutable artifact with params + metrics
        │
        v
Step 8: Save to Registry
  → registry.py: persist cards as JSON
  → Available for backtest optimization
```

---

## 4. Deployment Pipeline

**Entry:** `POST /api/strategies/{symbol}/v{ver}/promote`

```
┌─────────────┐     ┌──────────┐     ┌──────┐     ┌──────┐
│  CANDIDATE  │ ──→ │ DRY_RUN  │ ──→ │ DEMO │ ──→ │ LIVE │
│  (new)      │     │          │     │      │     │      │
└─────────────┘     └──────────┘     └──────┘     └──────┘

Promotion Gates:
  candidate → dry_run:  No requirements (auto after creation)
  dry_run → demo:       30+ trades, Sharpe > 0.3
  demo → live:          50+ trades, Sharpe > 0.5
  activate (live):      100+ trades, Sharpe > 0.7

Canary Deployment (optional):
  POST .../canary/start → allocate % of capital to new version
  POST .../canary/end   → evaluate → "promote" or "reject"
```

---

## 5. AI Override Hooks

AI can influence each pipeline stage. Signal mode is set per-strategy on the Strategy Card.
Three modes exist: `algo_only`, `algo_ai`, and `ai_signal`. `algo_ai` is the mode formerly
referred to as `algo_plus_ai` in older drafts; there is no `ai_only` mode.

| Stage | AI Role | Default Model | When Active |
|-------|---------|---------------|-------------|
| Signal generation | `signal` | `gemini-2.5-flash-lite` | `ai_signal` mode only |
| Signal validation | `validator` | `claude-haiku-4-5-20251001` | `algo_ai` and `ai_signal` modes (Stage 6) |
| Research analysis | `research` | `gemini-2.5-pro` | Background MetaLoop analysis |
| Parameter suggestion | `param_suggest` | `deepseek-reasoner` | Background MetaLoop optimization |
| Regime classification | `regime` | `gemini-2.5-flash-lite` | Hourly background task |
| Provider fallback | `fallback` | `grok-3-mini` | When primary provider is down |

**Signal modes:**
- `algo_only` — AlgorithmicSignalEngine only; no AI call; validation via structural invalidation only
- `algo_ai` — AlgorithmicSignalEngine + FeaturePipeline (22 tools); optional AI validator at Stage 6
- `ai_signal` — LLM generates direction hypothesis; AI validator at Stage 6 is required

**Model overrides set via:**
- `PUT /api/strategies/{symbol}/v{version}/models` — accepts all 6 role keys
- Stored in strategy version JSON under `ai_models{}`
- Falls back to `DEFAULT_ROLES` in `ai/model_hub.py`

**Model availability filtering:**
- `GET /api/test/models` returns only models from providers with a configured API key
- Ollama: live-pinged at `QWEN_LOCAL_BASE` (default `http://localhost:11434`) with 2 s timeout
- Response includes `roles[]` and `cost_tier` per model for UI population
- Strategy Card dropdowns are populated from this endpoint — prevents selecting unconfigured models
