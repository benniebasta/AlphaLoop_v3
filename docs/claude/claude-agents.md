# AlphaLoop v3 — System Agents

## Purpose
All runtime agents (Python components that run autonomously), their responsibilities, inputs, outputs, and event communication.

---

## Agent Overview

| Agent | File | Trigger | Lifecycle |
|-------|------|---------|-----------|
| TradingLoop | `trading/loop.py` | Poll interval (300s) | Long-running async task |
| MetaLoop | `trading/meta_loop.py` | TradeClosed event | Background asyncio.Task |
| HealthMonitor | `trading/health_monitor.py` | TradeClosed event | In-process, stateful |
| MicroLearner | `trading/micro_learner.py` | TradeClosed event | In-process, stateful |
| SignalEngine | `signals/engine.py` | Called by TradingLoop | Per-cycle |
| AlgorithmicEngine | `signals/algorithmic.py` | Called by TradingLoop (Mode A/B) | Per-cycle |
| SeedLabRunner | `seedlab/runner.py` | WebUI POST request | Background asyncio.Task |
| BacktestRunner | `backtester/runner.py` | WebUI POST request | Background asyncio.Task |
| ResearchAnalyzer | `research/analyzer.py` | Called by MetaLoop | Per-check |
| Repositioner | `risk/repositioner.py` | Position monitor tick | Continuous |

---

## TradingLoop

**File:** `src/alphaloop/trading/loop.py` (~450 lines)

**Cycle:**
1. Check RiskMonitor (kill switch, daily limits)
2. Load active strategy config (`strategy_loader.py`)
3. Build market context (OHLCV + indicators)
4. Run filter pipeline (tool plugins)
5. Branch by signal mode: AI or Algorithmic
6. Generate signal → TradeSignal
7. Validate signal (hard rules + optional AI)
8. Run risk guards (7 stateful guards)
9. Size position (ATR-based + confidence multiplier)
10. Execute order (MT5 or dry-run)
11. Log to DB + publish events + notify

**Events Published:** `SignalGenerated`, `SignalValidated`, `SignalRejected`, `TradeOpened`, `PipelineBlocked`

**Injected Dependencies:** signal_engine, validator, sizer, executor, risk_monitor, filter_pipeline, trade_repo, notifier, ai_caller, settings_service, tool_registry

---

## MetaLoop

**File:** `src/alphaloop/trading/meta_loop.py` (~220 lines)

**Purpose:** Background strategy evolution loop. Non-blocking.

**Trigger:** Subscribes to `TradeClosed` events. After every `check_interval` trades (default: 20):

**Cycle:**
1. Count recent closed trades
2. Run StrategyHealthMonitor
3. If `DEGRADING` or `CRITICAL`:
   - Run ResearchAnalyzer for performance analysis
   - Run auto-improvement (Optuna in thread pool)
   - Create new strategy version if improved
4. If `auto_activate` enabled: activate new version
5. Monitor via RollbackTracker

**RollbackTracker:**
- Records R-multiples (pnl / risk) per trade
- Computes rolling Sharpe over `rollback_window` trades
- Rolls back if current Sharpe < 70% of previous version's Sharpe

**Events Published:** `MetaLoopCompleted`, `StrategyRolledBack`

---

## StrategyHealthMonitor

**File:** `src/alphaloop/trading/health_monitor.py` (~130 lines)

**Formula:**
```
health_score = w_sharpe * sharpe_norm + w_winrate * winrate - w_drawdown * drawdown_norm - w_stagnation * stagnation_score
```

**Default Weights:** Sharpe=0.35, WinRate=0.25, Drawdown=0.25, Stagnation=0.15

**Thresholds:**
| Score | Status | Action |
|-------|--------|--------|
| > 0.6 | `HEALTHY` | No action |
| 0.3-0.6 | `DEGRADING` | Trigger retrain |
| < 0.3 | `CRITICAL` | Immediate rollback |

**Input:** Rolling window of TradeClosed events (default window=30)

---

## MicroLearner

**File:** `src/alphaloop/trading/micro_learner.py` (~175 lines)

**Purpose:** Lightweight per-trade parameter nudges without full Optuna retraining.

**Adjustments:**
1. **Confidence recal:** If high-confidence trades are losing, nudge `min_confidence` up
2. **SL distance:** If SL hit rate too high, nudge `sl_atr_mult` up
3. **Validation:** If rejection rate too high for a rule, relax threshold

**Guardrails:**
- Each adjustment capped at **±1%** of current value per trade
- Total drift from baseline capped at **±5%**
- Stored in DB as `micro_adjustments_{symbol}`
- Reset on full autolearn cycle (MetaLoop)

---

## MultiAssetSignalEngine

**File:** `src/alphaloop/signals/engine.py` (~153 lines)

**Input:** MarketContext + StrategyParams
**Output:** TradeSignal (Pydantic)

**Process:**
1. Build prompt from market context + strategy params
2. Call AI via `AICaller.call_role("signal")`
3. Parse JSON response → TradeSignal
4. Prompt injection detection in schema validation

---

## AlgorithmicSignalEngine

**File:** `src/alphaloop/signals/algorithmic.py` (~135 lines)

**Purpose:** Deterministic signal generation for Mode A/B (no AI).

**Logic:**
- EMA(21) vs EMA(55) crossover for direction
- RSI filter for overbought/oversold rejection
- ATR-based SL/TP calculation
- Confidence from signal strength metrics

---

## SeedLabRunner

**File:** `src/alphaloop/seedlab/runner.py`

**Pipeline:**
1. Generate seeds (template + optional combinatorial)
2. Detect market regimes
3. Run multi-regime backtest for each seed
4. Extract metrics per seed
5. Analyze stability across regimes
6. Score and rank seeds
7. Build strategy cards
8. Save to registry

**Events Published:** `SeedLabProgress`

**In-Memory State:** `_tasks`, `_stop_flags`, `_logs` (shared with BacktestRunner pattern)

---

## BacktestRunner

**File:** `src/alphaloop/backtester/runner.py`

**Process:**
1. Fetch OHLCV data (MT5 or yfinance)
2. Gen 1: Baseline with default params
3. Gen 2+: Optuna TPE (30 trials/gen) on 80% train split
4. Validate on 20% holdout — reject if train-val Sharpe gap > 0.30
5. Early stop: 2 consecutive gens without improvement
6. Checkpoint save/load for pause/resume

**Threading:** Entire Optuna loop runs via `asyncio.to_thread()` with `asyncio.run()` per trial — keeps server responsive.

---

## Event Bus Topology

```
TradingLoop ──→ SignalGenerated ──→ WebSocket, Metrics
            ──→ SignalValidated ──→ WebSocket
            ──→ SignalRejected  ──→ WebSocket
            ──→ TradeOpened     ──→ WebSocket, Telegram, Metrics
            ──→ PipelineBlocked ──→ WebSocket

MT5/Executor ──→ TradeClosed ──→ MetaLoop, MicroLearner, HealthMonitor, WebSocket, Telegram

MetaLoop ──→ MetaLoopCompleted  ──→ WebSocket
         ──→ StrategyRolledBack ──→ WebSocket, Telegram

RiskMonitor ──→ RiskLimitHit ──→ WebSocket, Telegram

SeedLabRunner ──→ SeedLabProgress ──→ WebSocket

SettingsService ──→ ConfigChanged ──→ WebSocket

DeploymentPipeline ──→ StrategyPromoted ──→ WebSocket
```
