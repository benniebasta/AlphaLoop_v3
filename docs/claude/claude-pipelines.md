# AlphaLoop v3 — Pipeline Flows

## Purpose
Step-by-step flow for each major pipeline: live trading, backtesting, SeedLab discovery, and deployment promotion.

---

## 1. Live Trading Pipeline

**Entry:** `TradingLoop.run_cycle()` in `src/alphaloop/trading/loop.py`

```
┌─ Step 1: Risk Check ──────────────────────────────────────────┐
│  RiskMonitor.check_kill_switch()                              │
│  RiskMonitor.check_daily_loss()                               │
│  RiskMonitor.check_consecutive_losses()                       │
│  → If blocked: log + sleep → next cycle                       │
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
┌─ Step 3: Market Context ─────────────────────────────────────┐
│  OHLCVFetcher.fetch(symbol, timeframe)                        │
│  → indicators: RSI, EMA(21/55/200), ATR, VWAP, BOS, FVG     │
│  → MarketContext Pydantic model                               │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 4: Filter Pipeline ────────────────────────────────────┐
│  FilterPipeline.run(context)                                  │
│  → session_filter → news_filter → volatility_filter →        │
│    dxy_filter → sentiment_filter → risk_filter               │
│  → Short-circuit on first block                               │
│  → If blocked: PipelineBlocked event → next cycle             │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 5: Signal Generation ──────────────────────────────────┐
│  Branch by signal_mode:                                       │
│  ├── "ai": MultiAssetSignalEngine.generate_signal()           │
│  │         → AICaller.call_role("signal") → parse JSON        │
│  └── "a"/"b": AlgorithmicSignalEngine.generate()              │
│               → EMA crossover + RSI filter                    │
│  → TradeSignal (direction, entry, SL, TP, confidence)         │
│  → SignalGenerated event                                      │
└───────────────────────────────────────────────────────────────┘
        │
        v
┌─ Step 6: Validation ─────────────────────────────────────────┐
│  HardRuleChecker.check_all(signal, context) — 13 rules        │
│  → If any fail: SignalRejected event → next cycle             │
│  UniversalValidator.validate(signal, context)                  │
│  → Optional AI validation (call_role("validator"))             │
│  → ValidatedSignal + SignalValidated event                    │
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

AI can influence each pipeline stage:

| Stage | AI Role | Override |
|-------|---------|----------|
| Signal generation | `signal` model | Per-strategy model override |
| Signal validation | `validator` model | Per-strategy model override |
| Research analysis | `research` model | Per-strategy model override |
| Auto-improvement | `autolearn` model | Per-strategy model override |

Model overrides set via:
- `PUT /api/strategies/{symbol}/v{version}/models`
- Stored in strategy version JSON
- Falls back to global defaults from AI Hub
