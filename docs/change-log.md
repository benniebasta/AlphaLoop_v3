# AlphaLoop v3 — Change Log

Timestamped record of major changes, bug postmortems, and architectural decisions.

---

## 2026-04-05 — v3 Validation Path Deleted

### Summary
Removed the v3 rule-based validation layer (`validation/rules.py`, `validation/validator.py`) as part of the institutional audit cleanup. These files were superseded by the v4 pipeline (Stages 4A–6 in `pipeline/orchestrator.py`) and contained the most critical safety gap in the system: `UniversalValidator` had an `algo_only` bypass that could disable all AI validation in live mode via a single config flag.

### Files Deleted
- `src/alphaloop/validation/rules.py` — `HardRuleChecker` (13 deterministic rules); superseded by `StructuralInvalidator` (Stage 4A) and `ExecutionGuard` (Stage 8)
- `src/alphaloop/validation/validator.py` — `UniversalValidator`; superseded by `BoundedAIValidator` (Stage 6); contained `algo_only` live bypass and silent degradation on unset config
- `tests/unit/test_hard_rules.py` — tests for deleted `HardRuleChecker`

### Imports Removed
- `main.py` — removed `UniversalValidator` import, instantiation, ECE state block, and `validator=` kwarg on `TradingLoop`
- `trading/loop.py` — removed `validator=None` param and `self.validator` assignment (was assigned but never called)
- `webui/routes/ai_hub.py` — `/api/ai-hub/calibration` endpoint simplified to static response (ECE calibration was tied to deleted validator)
- `tests/unit/test_new_modules.py` — removed 4 `HardRuleChecker` test classes
- `tests/e2e/test_lifecycle.py` — removed `test_hard_rules_all_13`

### Impact
- All 3 signal modes (`algo_only`, `algo_ai`, `ai_signal`) unaffected — they route through the v4 pipeline only
- Backtester unaffected — uses `vbt_engine` + `TradeConstructor`, no validator dependency
- `validation/prompts.py` retained — used by `BoundedAIValidator` in Stage 6
- `BoundedAIValidator` is now the sole validation path for AI-assisted modes

---

## 2026-04-05 — Backtest Engine Construction Parity + vbt_engine Wiring

### Summary
The `runner.py` had a longstanding TODO: live trading uses structure-derived SL via `TradeConstructor`, but the backtester still used flat ATR multipliers (`sl_atr_mult × ATR`). Backtested Sharpe therefore diverged from live behaviour. Fixed by wiring `vbt_engine.run_vectorbt_backtest` (which internally runs `TradeConstructor` on every bar) into the runner, replacing all `BacktestEngine` calls.

### Changes (`backtester/runner.py`)
- Replaced `from alphaloop.backtester.engine import BacktestEngine` with `from alphaloop.backtester.vbt_engine import run_vectorbt_backtest`
- Added `_run_vbt()` sync helper: converts numpy arrays → `pd.DataFrame`, calls `run_vectorbt_backtest`, returns `VBTBacktestResult`
- Replaced all `await _run_engine_in_thread(... signal_fn=..., ...)` calls with `await asyncio.to_thread(_run_vbt, ...)`
- Replaced `run_on_train` closure: removed `asyncio.run(engine.run(...))` with direct `_run_vbt()` (sync, no event loop overhead in Optuna thread)
- Removed all `result.summary()` dict access — use `VBTBacktestResult` attributes directly (`.trade_count`, `.win_rate`, `.sharpe`, `.total_pnl`, `.max_drawdown_pct`)
- Removed all `len(result.closed_trades)` references → `result.trade_count`
- Removed the TODO comment from module docstring
- `_run_engine_in_thread` kept as a `NotImplementedError` stub to surface any missed call sites

**Result:** Backtest SL/TP now uses the same constraint-first `TradeConstructor` logic as the live v4 pipeline. Backtested Sharpe is no longer inflated by unrealistic flat-ATR stops.

---

## 2026-04-05 — v4 Pipeline: 24-Plugin Stage Wiring + Constraint-First Construction (Parts 1–3)

### Summary
Three-part pipeline hardening milestone. Part 1 replaced the old SL/TP guess-and-validate approach with constraint-first construction. Part 2 integrated vectorbt + Optuna so backtests run the same logic as live. Part 3 wired all 24 tool plugins into their correct pipeline stages, making every strategy card toggle functional.

---

### Part 1 — Constraint-First Trade Construction (`pipeline/construction.py`)

**Problem:** SL/TP were derived from flat multipliers then checked by StructuralInvalidator. Any violation required starting over. This produced excessive soft-invalidation penalties and mis-priced entries.

**Fix:** `TradeConstructor` now derives SL from the nearest market structure (ATR stop → FVG → BOS → swing, in priority order). TP is computed from R:R after SL is fixed. Invalidation becomes a pure safety-net — it should almost never fire if construction did its job.

**Key files:**
- `pipeline/construction.py` — `TradeConstructor` class with `construct()` returning `ConstructionResult`
- `pipeline/types.py` — `CandidateSignal.sl_source` field added
- `pipeline/defaults.py` — `INVALIDATION` thresholds updated

---

### Part 2 — vectorbt Backtest Engine + Optuna Optimiser (`backtester/vbt_engine.py`)

**Problem:** `BacktestEngine` used the old custom bar-loop with flat SL/TP multipliers. Backtest performance didn't match what live trading would produce with the v4 construction path.

**Fix:** New `vbt_engine.py` runs `TradeConstructor` on every bar in the same way the live pipeline does. Optuna optimiser (`backtester/optimizer.py`) searches construction params (ATR mult, entry zone, SL buffer, TP R:R) rather than just signal thresholds.

**Key files:**
- `backtester/vbt_engine.py` — `run_vectorbt_backtest()`, `VBTBacktestResult`
- `backtester/optimizer.py` — Optuna integration with `VBTOptunaOptimizer`
- `signals/algorithmic.py` — `compute_direction()` extracted from runner, shared by live + backtest

---

### Part 3 — All 24 Tool Plugins Wired to Correct Pipeline Stages (`tools/registry.py`)

**Problem:** `FilterPipeline` was bypassed by an early `return` in `loop.py`. All 24 tool plugins were registered but never called in the live v4 cycle. Strategy card toggles had no effect.

**Fix:** `STAGE_TOOL_MAP` assigns each plugin to exactly one pipeline stage. `TradingLoop._get_stage_tools(stage)` filters by strategy card toggle before injecting into each stage constructor. Every plugin now runs in its semantically correct stage.

| Stage | Plugins wired |
|---|---|
| Stage 1 MarketGate | `session_filter`, `news_filter`, `volatility_filter` |
| Stage 2 Regime | `adx_filter`, `choppiness_index`, `trendilo` |
| Stage 3 Hypothesis | `ema_crossover`, `macd_filter`, `rsi_feature`, `fast_fingers` |
| Stage 3B Construction | `swing_structure`, `fvg_guard`, `bos_guard` |
| Stage 4A Invalidation | `liq_vacuum_guard`, `vwap_guard` |
| Stage 4B Quality | `ema200_filter`, `alma_filter`, `bollinger_filter`, `volume_filter`, `dxy_filter`, `sentiment_filter` |
| Stage 7 RiskGate | `risk_filter`, `correlation_guard` |
| Stage 8 ExecGuard | `tick_jump_guard` |

**Hardcoded checks removed from MarketGate:** weekend, news blackout, dead-market ATR% — now fully owned by `session_filter`, `news_filter`, `volatility_filter` plugins. The 5 infrastructure checks (kill switch, stale feed, missing bars, feed desync, abnormal spread) remain hardcoded.

**Key files:**
- `tools/registry.py` — `STAGE_TOOL_MAP` constant
- `trading/loop.py` — `_get_stage_tools()` helper, updated `_build_v4_orchestrator()`
- `pipeline/market_gate.py` — `tools: list` param, plugin loop after infra checks, 3 hardcoded checks removed
- `pipeline/regime.py` — `tools: list` param, annotation loop (non-blocking)
- `pipeline/orchestrator.py` — `hypothesis_tools` param, context.tool_results T5 injection, construction tool loop
- `pipeline/construction.py` — `tools: list` param, `structural_warnings` field on result
- `pipeline/invalidation.py` — plugin loop for `liq_vacuum_guard`, `vwap_guard`
- `pipeline/risk_gate.py` — `risk_filter_tool` param
- `pipeline/execution_guard.py` — `tick_jump_tool`, `liq_vacuum_tool` params with indicator fallback
- `signals/engine.py` — `context.tool_results` T5 fallback

**Tests:**
- `tests/unit/test_tool_wiring.py` — 17 new tests: per-stage plugin blocking, annotation-only for regime, STAGE_TOOL_MAP integrity (all 24 assigned, no duplicates, quality has 6)
- Test count: 367 → 412 passing (0 regressions)

**Docs:**
- `docs/references/trading-modes.md` — algo_only / algo_ai / ai_signal mode cycle paths

---

## 2026-03-31 — Institutional Score Phase 4: Data Feed + Rigor + Polish (88 → 90)

### Summary
Phase 4 completes the institutional hardening roadmap. Adds MT5 high-frequency tick aggregation, walk-forward holdout enforcement for DEMO→LIVE promotion, ECE confidence calibration tracking, dynamic correlation matrix, and a fully instrumented 5-step graceful shutdown sequence.

---

### Feature 4.1 — MT5 TickAggregator (`data/tick_aggregator.py`) ★ NEW FILE

**Problem:** `LiveFeed` polled MT5 every 5 seconds — up to 5000ms price lag, unacceptable for tight entry precision.

**Fix:** New `TickAggregator` class bridges MT5's synchronous API into asyncio via a background daemon thread + `asyncio.Queue`.

**Architecture:**
```
Background thread (daemon)          Main asyncio event loop
  while not stop:                     LiveFeed._run_aggregator_loop()
    for sym in symbols:    ──→   asyncio.Queue  ──→  _ticks[sym] = latest
      mt5.symbol_info_tick()
    sleep(0.1s)                    (drain queue, update cache)
```

| Mode | Typical latency |
|------|----------------|
| yfinance 5s poll | 0–5000ms |
| TickAggregator 100ms | 0–100ms |

**Integration:** `LiveFeed.start()` now tries `TickAggregator` first. If MT5 is unavailable, falls back to 5s yfinance loop — no code changes needed at call sites.

**Reliability:** Broad `except (ImportError, Exception)` ensures yfinance-only environments work unchanged. Queue `maxsize=100` prevents unbounded memory on slow consumers.

---

### Feature 4.2 — Walk-Forward Holdout Enforcement (`backtester/`)

**Problem:** DEMO→LIVE promotion had no out-of-sample holdout requirement — strategies could overfit in-sample.

**Fix (deployment_pipeline.py):**
- `evaluate_promotion(holdout_result=...)` now required for `DEMO → LIVE`
- Blocks if `holdout_result is None` with clear message
- Blocks if `holdout_result["sharpe"] < 0.3` (minimum out-of-sample Sharpe)

**Fix (engine.py):**
- New `run_holdout_validation(holdout_bars=500)` method on `BacktestEngine`
- Runs the final N bars as a strictly non-overlapping holdout slice
- Returns summary dict directly compatible with `evaluate_promotion(holdout_result=...)`

**Usage:**
```python
holdout = await engine.run_holdout_validation(symbol, opens, highs, lows, closes, signal_fn=signal_fn)
result = await pipeline.evaluate_promotion(StrategyStatus.DEMO, metrics, holdout_result=holdout)
```

---

### Feature 4.3 — Graceful Shutdown Sequence (`main.py`)

**Problem:** SIGINT/SIGTERM only called `trading_loop.stop()` — guard state was lost and open positions were unreconciled on crash.

**Fix:** Finally block now runs a 5-step shutdown sequence:

| Step | Action |
|------|--------|
| 1 | `save_guard_state()` — persist hash filter, conf variance, spread regime, equity scaler, DD pause to DB |
| 2 | `PositionReconciler.reconcile()` — detect crash orphans (non-dry-run only) |
| 3 | `metrics_tracker.record_sync("graceful_shutdown", 1)` — Prometheus marker |
| 4 | `_redis_sync.close()` — clean Redis disconnection |
| 5 | Unregister instance from `RunningInstance` DB table |

---

### Feature 4.4 — Dynamic Correlation Matrix (`tools/plugins/correlation_guard/`)

**Problem:** `CorrelationGuard` used a hardcoded static 12-pair map — stale correlations in changing market regimes.

**Fix:**
- New `CorrelationMatrixUpdater` (`updater.py`) fetches 60-day daily closes via yfinance, computes pandas `pct_change().corr()`, flattens to `"SYM_A|SYM_B"` → float dict
- `update_and_persist(settings_service)` saves matrix to DB + **immediately updates the in-memory `_DYNAMIC_MATRIX` cache** in `tool.py`
- `CorrelationGuard.run()` reads dynamic matrix first, falls back to static map if absent
- Weekly background task in `webui/app.py` lifespan auto-refreshes the matrix every 7 days

---

### Feature 4.5 — ECE Confidence Calibration (`validation/validator.py` + `webui/routes/ai_hub.py`)

**Problem:** AI `risk_score` output was uncalibrated — no way to detect if AI overconfidence was leading to bad approvals.

**Fix:**
- `UniversalValidator` now maintains `_calibration_log: deque[tuple[float, bool]]` (maxlen=500)
- Each AI validation records `(1.0 - risk_score, approved_bool)` as a (confidence, outcome) pair
- `compute_ece(n_bins=10)` computes Expected Calibration Error: `Σ |mean_accuracy - mean_confidence| × weight`
- Logs WARNING when ECE > 0.10 (every 20 samples after n ≥ 50)
- `get_calibration_summary()` returns full calibration curve for the UI
- `GET /api/ai-hub/calibration` exposes ECE + calibration curve

**UI:** AI Hub now shows an ECE badge in the Model Performance section header:
- 🟢 `ECE 0.045 ✓ calibrated` — green badge if ECE ≤ 0.10
- 🔴 `ECE 0.182 ⚠ drift` — red badge if ECE > 0.10

**Module-level validator reference:** `_global_validator_ref` in `validator.py` is set by `main.py` to expose the active validator instance to the WebUI calibration endpoint without DI machinery.

---

### Config — Coverage Enforcement (`pyproject.toml`)

Added `[tool.coverage.report]` with `fail_under = 80` + `[tool.coverage.run]` source config (done in Phase 2 section, documented here for completeness).

---

## 2026-03-31 — Institutional Score Phase 1: Observability & Critical Fixes (72 → 79)

### Summary
Phase 1 of the institutional-grade hardening roadmap. Fixes a runtime `ImportError` on `/metrics`, wires compliance breach reporting, adds AI model performance tracking, and fixes two silent error-swallowing bugs.

---

### Fix 1.1 — MetricsTracker Singleton (`monitoring/metrics.py`) ★ CRITICAL

**Problem:** `webui/app.py` imported `metrics_tracker` by name but no such name existed in `metrics.py` — only module-level functions. This caused an `ImportError` at server startup, making `/metrics` permanently unreachable.

**Fix:** Added `MetricsTracker` class that delegates to the existing module-level functions without replacing them. Added `metrics_tracker = MetricsTracker()` singleton at the bottom of `metrics.py`.

**Recording wired in:**
- `trading/loop.py` — `cycle_duration_ms` (finally block) + `validation_latency_ms` (around validator)
- `execution/mt5_executor.py` — `slippage_pips` after MT5 fill
- `validation/validator.py` — `approval_count` / `rejection_count`

**UI:** `/metrics` Prometheus endpoint link added to WebUI sidebar (System → 📊 Metrics).

---

### Fix 1.2 — Compliance Breach Report (`compliance/reporting.py`)

**Problem:** `risk_breach_report()` returned `{"status": "framework_ready"}` — never populated. `RiskLimitHit` events fired but nothing captured them.

**Fix:**
- Added `_breach_log: deque(maxlen=500)` to `ComplianceReporter.__init__`
- Added `record_breach(event)` — appends `{timestamp, symbol, limit_type, details}`
- Implemented `risk_breach_report(days)` — filters by date, counts by `limit_type`, returns raw list
- `main.py`: subscribed `reporter.record_breach` to `RiskLimitHit` event bus

---

### Feature 1.3 — AI Model Performance Tracker (`ai/performance.py`) ★ NEW FILE

**New:** `ModelPerformanceTracker` class with per-model rolling deques (maxlen=200).

- Tracks: `call_count`, `avg_latency_ms`, `p95_latency_ms`, `error_rate`, `success_rate`
- `get_worst_model(min_calls=10)` — returns model_id with highest error rate
- Auto-wired in `ai/caller.py` `finally` block on every `call_model()` invocation
- Exposed at `GET /api/ai-hub/performance`

**UI:** AI Hub page now shows **Model Performance** table with per-model stats. Worst model highlighted in amber with ⚠ icon.

---

### Bug Fix B1 — WebSocket Error Swallowing (`webui/routes/websocket.py`)

**Problem:** Bare `except Exception:` blocks in broadcast loop silently dropped send failures and `asdict()` errors.

**Fix:** Changed to `except Exception as e:` with `logger.debug()` calls, so failures are visible in debug logs without crashing the broadcast loop.

---

### Bug Fix B2 — SettingsService Init Failure (`webui/routes/bots.py`)

**Problem:** `except Exception: pass` silently ignored `SettingsService` init failures — gave no indication of why bot state wasn't loading.

**Fix:** Changed to `except Exception as e: logger.warning("[bots] SettingsService init failed: %s", e)`.

---

## 2026-03-31 — Institutional Score Phase 3: Execution Quality (85 → 88)

### Summary
Phase 3 adds limit order support, Transaction Cost Analysis, and P&L factor attribution to closed trades.

---

### Feature 3.1 — Limit Orders (`execution/mt5_executor.py`)

**New method:** `place_limit_order(direction, lots, limit_price, sl, tp, expiry_hours)`

Uses `TRADE_ACTION_PENDING` + `ORDER_TYPE_BUY_LIMIT/SELL_LIMIT` with `ORDER_TIME_SPECIFIED` expiry.
Order lifecycle tracked via `OrderRegistry` identical to market orders.
Dry-run: immediately returns `success=True` with `fill_price = limit_price`.

---

### Feature 3.2 — TCA Analyzer (`execution/tca.py`) ★ NEW FILE

**New:** `TCAAnalyzer` computes execution quality from closed trade history.

Metrics: avg/max slippage, spread cost USD, slippage vs ATR%, execution quality score (0–100).
Exposed at `GET /api/execution/tca`.

**UI:** Trades page now shows a TCA quality score bar + metrics row at the top.

---

### Feature 3.3 — Factor Attribution (`research/attribution.py`) ★ NEW FILE

**New:** `TradeAttributor` decomposes trade PnL into entry skill, exit skill, slippage cost, and spread cost. All four components stored in existing DB columns (`pnl_entry_skill`, `pnl_exit_skill`, `pnl_slippage_usd`, `pnl_commission_usd`).

- Auto-computed in `TradingLoop.record_trade_close(trade_id, trade_data)`
- Backfill historical trades via `POST /api/execution/attribution/backfill`
- `TradeRepository.update_attribution()` added for targeted column updates

**UI:** Trades table rows are now expandable — clicking shows the P&L attribution panel per trade.

---

## 2026-03-31 — Institutional Score Phase 2: Risk Hardening (79 → 85)

### Summary
Phase 2 of the institutional-grade hardening roadmap. Adds probabilistic VaR/CVaR risk estimates, scenario-based stress testing, and optional Redis HA state replication alongside the existing rule-based risk monitor.

---

### Feature 2.1 — Historical VaR and CVaR (`risk/var_calculator.py`) ★ NEW FILE

**New:** `HistoricalVaRCalculator` class using stdlib-only historical simulation.

- `fit(pnl_series)` — fits on last 252 closed trades
- `var(confidence)` — percentile-based VaR (95% or 99%)
- `cvar(confidence)` — Expected Shortfall (mean of worst-5% outcomes)
- `var_breach(pnl)` — advisory check: True if PnL exceeded threshold

**Integration:**
- `RiskMonitor.seed_from_db()` now fits the calculator on 1000 closed trades
- `RiskMonitor.status` exposes `var_95`, `cvar_95`, `var_99`, `cvar_99`, `var_observations`, `var_breach_today`
- `GET /api/risk` returns all VaR fields
- VaR breach logs a WARNING but does not block trading (advisory)

**UI:** Risk Dashboard shows VaR/CVaR gauges with green/amber/red color coding. Breach alert banner appears if daily PnL exceeds VaR threshold.

---

### Feature 2.2 — Redis HA State Replication (`risk/redis_state.py`) ★ NEW FILE

**New:** `RedisStateSync` class for optional in-memory state caching.

- Enabled only when `REDIS_URL` env var is set
- Pushes `RiskMonitor.status` to Redis every 10 trading cycles (TTL: 1 hour)
- On startup: reads cached state if DB seed hasn't completed yet
- Gracefully disabled if Redis is unreachable (no exception propagates)

**Architecture:** Redis is a cache layer only. SQLite/Postgres remains authoritative. `guard_persistence.py` write-path unchanged.

**Dependency added:** `redis>=5.0.0` in `pyproject.toml` `[redis]` optional extras.

---

### Feature 2.3 — Stress Test Scenarios (`risk/stress_tester.py`) ★ NEW FILE

**New:** `StressTester` with 3 built-in market shock scenarios.

| Scenario | Description |
|----------|-------------|
| `COVID_GAP` | Single -10% bar shock |
| `RATE_HIKE_SEQUENCE` | 3× -2% consecutive bars |
| `FLASH_CRASH` | -5% then +4% recovery |

- `run_scenario()` applies shocks to current balance, returns loss USD/%, final equity, margin call risk
- `margin_call_risk` = True if simulated equity < 20% of starting balance
- `GET /api/risk/stress` exposes all scenarios

**UI:** Risk Dashboard adds Stress Test Scenarios table with scenario name, simulated loss, % loss, final equity, and margin call risk badge.

---

### Config — Coverage enforcement (`pyproject.toml`)

Added `[tool.coverage.report]` with `fail_under = 80` and `[tool.coverage.run]` source config. Running `pytest --cov --cov-fail-under=80` will now fail if coverage drops below 80%.

---

## 2026-03-31 — Windows Popup Terminal Fix (`bots.py`)

### Summary
A cmd/terminal window was flashing on screen while agents were running.

### Root Causes
Two separate sources:

1. **`_pid_alive()` spawning `tasklist`** — the `/bots` endpoint is polled frequently by the UI; each call ran `subprocess.run(["tasklist", ...])` which on Windows creates a visible console window even with `CREATE_NO_WINDOW` in some configurations.

2. **Agent subprocess using `python.exe`** — launching agents via `sys.executable` resolves to `python.exe`, which is a console-subsystem application. Windows may flash a console window on startup regardless of creation flags.

### Fixes
1. **`_pid_alive`** — replaced `tasklist` subprocess entirely with a direct Windows API call via `ctypes` (`OpenProcess` + `GetExitCodeProcess`). No subprocess spawned at all.
2. **Agent launch** — replaced `sys.executable` with `pythonw.exe` (the windowless Python variant). Falls back to `python.exe` if `pythonw.exe` is not found.

---

## 2026-03-31 — Strategy Quality Gates + Auto-Retire

### Summary
Three improvements to prevent unusable strategy cards from cluttering the Strategies page and Alpha Agents deploy modal.

### Change 1 — Tightened candidate→dry_run promotion gate (`deployment_pipeline.py`)

| Metric | Before | After |
|--------|--------|-------|
| Min trades | 30 | 40 |
| Min Sharpe | 0.3 | 1.0 |
| Min win rate | 40% | 42% |
| Max drawdown | *(none)* | -25% |

### Change 2 — Auto-retire weak strategies at creation (`runner.py`)
After a backtest completes, the result is checked against the candidate→dry_run gate before the JSON file is written. If any threshold fails, the strategy is saved with `status: "retired"` instead of `"candidate"`. A warning log line explains which thresholds failed, e.g.:
> `⚠ Strategy below promotion threshold — auto-retiring (sharpe 0.28 < 1.0, WR 38.0% < 42.0%)`

Retired cards are filtered out of the Strategies page and the Alpha Agents deploy modal automatically.

### Change 3 — Strategy card: backtest context fields (`asset_trainer.py`, `strategies.js`)
Strategy version JSON `summary` block now stores `timeframe`, `days`, and `initial_capital` from the backtest run. Strategy cards display a context row below the name:
> `📊 1h   📅 365d   💰 $10,000`

Row is omitted for cards created before this change.

---

## 2026-03-31 — Alpha Agents Deploy Modal: Candidate Strategy Visibility (`bots.js`)

### Summary
The deploy modal showed "No strategies for this symbol. Train one in SeedLab first." even when candidate-status strategies existed — because candidates were filtered out.

### Fix
- Candidates now appear in the strategy picker but are rendered greyed-out (opacity 0.55) with the radio input disabled and a "— promote to deploy" label next to their status badge.
- The empty-state message is updated to "No strategies found for this symbol. Train one in SeedLab first." to distinguish from the case where strategies exist but are all candidates.
- The filter now only excludes `retired` strategies (not candidates).

---

## 2026-03-31 — MT5 Backtest Data Fetcher Fixes

### Summary
Three bugs in `backtester/runner.py` `_fetch_data_mt5` that caused incorrect
date windows, silent failures, and misleading bar counts.

### Bug 1 — Wrong fetch method (bars-based vs date-based)
`copy_rates_from_pos` was used with a `bars_needed` count calculated assuming
24/7 trading. For instruments like XAUUSD (5d/week), this caused MT5 to look
back 18 months to collect 365 days' worth of bars. Fixed by switching to
`copy_rates_range(date_from, date_to)` using an explicit calendar window.

### Bug 2 — Silent MT5 init/symbol failure
Init failures and per-symbol `copy_rates` failures returned `None` with no
diagnostic. Fixed by logging `mt5.last_error()` on both `mt5.initialize()`
failure and on each symbol variant attempt (`BTCUSD`, `BTCUSDm`, etc.).

### Bug 3 — MT5 100k bar cap truncation (crypto / short timeframes)
MT5 caps `copy_rates_range` at 100,000 bars per call. For BTCUSD 5m × 365d
(105,120 bars needed) this silently truncated ~18 days from the start of the
window. Fixed by detecting the cap and emitting a specific warning with
calculated alternatives, e.g.:
> `use 5m with ≤347d, or switch to 15m for full 365d coverage`

---

## 2026-03-30 — Live Trading Tool Plugin Parity

### Summary
Eight filter tools that existed only in the backtest `make_signal_fn` (or were
registered as live plugins but never computed their context keys) are now fully
wired into the live trading loop.  The live pipeline now has indicator-level
parity with the backtest for all standard filters.

### Root Cause
`_build_context()` in `trading/loop.py` computed only RSI, ATR, and the two
param-driven EMAs on M15.  Twelve additional indicators referenced by live
plugins were never computed, causing those plugins to silently fail-open on
every cycle (Category 2) or have no plugin at all (Category 3).

### Category 2 — context keys added (plugins already existed, now active)

| Plugin | Key added | Computation |
|--------|-----------|-------------|
| `bos_guard`  | `m15_ind["bos"]`  | Close vs 20-bar swing high/low; break size in ATR |
| `fvg_guard`  | `m15_ind["fvg"]`  | Scan last 20 bars for 3-candle bullish/bearish gaps |
| `vwap_guard` | `m15_ind["vwap"]` | Typical-price VWAP proxy over last 50 M15 bars; volume-weighted when broker provides `tick_volume` |

### Category 3 — new plugins created and wired end-to-end

| Plugin | Context key | Logic |
|--------|-------------|-------|
| `ema200_filter`    | `ema200`           | Price must be above/below EMA(200); fail-open if < 200 bars |
| `macd_filter`      | `macd_histogram`   | Histogram sign must match direction |
| `bollinger_filter` | `bb_pct_b`, `bb_upper`, `bb_lower` | BUY: %B < 0.7; SELL: %B > 0.3 |
| `adx_filter`       | `adx`              | Block if ADX < 20 (ranging market); matches `_adx_simple` from backtester |
| `volume_filter`    | `volume_ratio`     | Block if current vol < 0.8× 20-bar mean; fail-open when no tick_volume |
| `swing_structure`  | `swing_structure`  | BUY requires "bullish" (HH+HL); SELL requires "bearish" (LH+LL); "ranging" blocks all |
| `tick_jump_guard`  | `tick_jump_atr`    | Block if 2-bar price move > 0.8× ATR |
| `liq_vacuum_guard` | `liq_vacuum`       | Block if bar range > 2.5× ATR AND body < 30% of range |

### Changes

**`trading/loop.py` — `_build_context()` M15 section:**
- Added `opens = df["open"].values` extraction
- Increased bar fetch from `max(ema_slow*3, 150)` to `max(ema_slow*3, 250)` to support EMA200 warmup
- Computes all 12 new indicator keys and merges them into `m15_ind`
- ADX computation is an exact port of `_adx_simple()` from `backtester/runner.py`
- VWAP uses volume-weighted typical price when `tick_volume` is present, SMA proxy otherwise
- Swing structure algorithm is an exact port of `_swing_structure_simple()` from backtester

**`tools/plugins/ema200_filter/tool.py`** — new
**`tools/plugins/macd_filter/tool.py`** — new
**`tools/plugins/bollinger_filter/tool.py`** — new
**`tools/plugins/adx_filter/tool.py`** — new
**`tools/plugins/volume_filter/tool.py`** — new
**`tools/plugins/swing_structure/tool.py`** — new
**`tools/plugins/tick_jump_guard/tool.py`** — new
**`tools/plugins/liq_vacuum_guard/tool.py`** — new

**`tools/registry.py` — `_DEFAULT_ORDER`:**
- New tools inserted at positions 7–14; existing structural guards shifted to 15–18

---

## 2026-03-31 — SeedLab Optimization Engine Overhaul + WebUI Bug Fixes

### Summary
Ten bugs fixed across the backtest runner, signal function, and WebUI layer.
The most critical was an O(n²) indicator computation that made large-bar backtests
take hours. Combined with a broken validation gate, Optuna was effectively disabled
— it could never accept any optimized parameters.

### Bug 1 — O(n²) EMA/RSI computation in `signal_fn` (`backtester/runner.py`)
`make_signal_fn` recomputed `_ema(closes[:i+1], ...)` and `_rsi(closes[:i+1], ...)`
from scratch at every bar using a Python for-loop. For 100k bars this is ~10 billion
iterations — effectively an infinite hang. Fixed by pre-computing all indicator arrays
once on the full price array (using a closure cache keyed by `id(closes)`) and
indexing at `[i]`. O(n²) → O(n). Same fix applied to EMA200 and MACD arrays.

### Bug 2 — BOS/FVG/ADX/Swing passed full-history slices (`backtester/runner.py`)
`_detect_bos_simple(highs[:i+1], ...)`, `_has_fvg(highs[:i+1], ...)`,
`_adx_simple(highs[:i+1], ...)`, and `_swing_structure_simple(highs[:i+1], ...)`
each received a growing array at every crossover bar despite only using the last
20–40 bars internally. Fixed by passing windowed slices: `highs[max(0, i-21):i+1]`,
`highs[max(0, i-40):i+1]`, and `highs[max(0, i-period*3):i+1]` respectively.

### Bug 3 — Validation gap always ~1005 (broken overfit gate) (`backtester/runner.py`)
The validation split (20% of data) consistently had fewer than 20 trades for sparse
signals, causing `val_result.sharpe = None → -999.0`. The gap formula
`train_sharpe - (-999)` always exceeded `OVERFIT_GAP_THRESHOLD = 0.30`, so Optuna's
best params were discarded every generation. Fixed by checking
`len(val_result.closed_trades) < MIN_TRADES` before computing the gap — if val has
insufficient trades the overfit check is skipped and the result goes straight to
full-data confirmation.

### Bug 4 — MIN_TRADES guard missing from Optuna objective (`optimizer.py`, `runner.py`)
Optuna trials with 2–5 trades could return artificially high Sharpe ratios (noise).
Added `MIN_TRADES = 20` constant. `run_on_train` now returns `-999.0` if
`len(r.closed_trades) < MIN_TRADES`. Baseline also logs a warning when trade count
is below threshold with guidance on which settings to adjust.

### Bug 5 — Early stop threshold too aggressive (`backtester/runner.py`)
Early stop fired after 2 consecutive no-improvement generations — too low for
10-gen runs where validation noise can mask real improvements. Raised to 3.

### Bug 6 — Uptime counter reset on page refresh (`webui/static/js/components/bots.js`)
`agentCard()` used `Date.now()` as `data-start`, resetting the counter to 0:00 on
every re-render. Fixed to use `b.started_at` from the API.
Initial display was also hardcoded `0:00` — fixed to pre-compute elapsed time at
render using `formatTick(Math.floor((Date.now() - startMs) / 1000))`.

### Bug 7 — Uptime 7-hour timezone offset (`webui/routes/bots.py`)
`started_at.isoformat()` returned a naive UTC string (no `Z` suffix). JavaScript's
`new Date()` parsed it as local time, causing a systematic offset equal to the
machine's UTC offset (UTC+7 → 7-hour drift). Fixed: appended `"Z"` to the
isoformat string.

### Bug 8 — SeedLab timeframe change did not update history days (`backtests.js`)
Changing the timeframe select had no effect on the History (Days) input. Added
`_mt5MaxDaysMap` matching the backend `_MT5_MAX_DAYS` constant. On timeframe change,
`days` auto-fills to `min(mt5Max, 365)` and `input.max` is set to `mt5Max`.
A yfinance fallback hint is shown when the entered value exceeds yfinance limits.

### Bug 9 — JS cache-bust version misaligned (`index.html`, `agents.js`, `seedlab.js`)
`app.js` contained `const _V = '6.5'` but `index.html` referenced `app.js?v=6.5`
and was never updated when `_V` was bumped. Browser served stale module versions.
Re-export shims (`agents.js`, `seedlab.js`) imported their targets without version
params, bypassing the cache-bust entirely. Fixed by keeping `index.html` and all
re-export imports in sync with `_V`.

### Files Changed
- `src/alphaloop/backtester/runner.py` — `make_signal_fn`, `run_on_train`, validation gate, early stop, baseline warning
- `src/alphaloop/backtester/optimizer.py` — `MIN_TRADES` constant
- `src/alphaloop/webui/routes/bots.py` — `started_at` UTC suffix
- `src/alphaloop/webui/static/js/components/bots.js` — uptime counter
- `src/alphaloop/webui/static/js/components/backtests.js` — timeframe → days auto-fill
- `src/alphaloop/webui/static/js/components/agents.js` — cache-bust version
- `src/alphaloop/webui/static/js/components/seedlab.js` — cache-bust version
- `src/alphaloop/webui/static/js/app.js` — `_V` bump to 6.9
- `src/alphaloop/webui/static/index.html` — `app.js?v=6.9`

---

## 2026-03-31 — BUG: Backtest-to-Live EMA/RSI Period Mismatch

### Root Cause
`_build_context()` in `trading/loop.py` always hardcoded EMA(21), EMA(55), RSI(14), ATR(14)
regardless of what Optuna found during backtesting. If the optimizer tuned `ema_fast=17` and
`ema_slow=63`, the live bot still computed EMA(21)/EMA(55) — a completely different signal.

Additionally, three AI prompts were reading `h1.get('ema21')` / `h1.get('ema55')` but the H1
context dict has always stored those values under `ema_fast` / `ema_slow` keys — meaning the
H1 EMA values in both the signal prompt and validation prompt have been `None` since launch.

### Changes

**`trading/loop.py` — `_build_context()` M15 section:**
- Reads `ema_fast`, `ema_slow`, `rsi_period`, `atr_period` from `self._active_strategy.params`
- Falls back to defaults (21, 55, 14, 14) when no strategy is loaded
- Dynamically sizes bar fetch: `max(ema_slow * 3, 150)` so slower EMAs get enough warmup
- Stores results as `ema_fast` / `ema_slow` keys (+ `ema_fast_period` / `ema_slow_period` for display)

**`signals/algorithmic.py` — `AlgorithmicSignalEngine.generate_signal()`:**
- Reads `m15.get("ema_fast")` / `m15.get("ema_slow")` instead of hardcoded `ema21`/`ema55`
- Signal reasoning string now shows actual periods (e.g. "EMA17/63 crossover...")
- Debug log updated to match new key names

**`signals/engine.py` — AI signal prompt H1 section:**
- `ema21` / `ema55` → `ema_fast` / `ema_slow` (fixes `None` values in AI context)
- M15 section now shows actual period numbers: `EMA(17): 2648.5 | EMA(63): 2641.2`

**`validation/prompts.py` — AI validation prompt H1 section:**
- Same fix: `ema21` / `ema55` → `ema_fast` / `ema_slow`

**Files modified:** `trading/loop.py`, `signals/algorithmic.py`, `signals/engine.py`, `validation/prompts.py`

---

## 2026-03-31 — AI Model Role Defaults Revamped

### Summary
`ROLES` tuple expanded from 4 to 6 roles. `DEFAULT_ROLES` updated to the cheapest
model that is fit-for-purpose per role. New Ollama models set to `enabled=True`.
`asset_trainer.py` and `core/config.py` updated to match.

### Changes

**`ai/model_hub.py` — ROLES and DEFAULT_ROLES:**

| Role | Old Default | New Default | Rationale |
|------|-------------|-------------|-----------|
| `signal` | *(new name for prior signal role)* | `gemini-2.5-flash-lite` | Free, fast, ~300 tokens/call |
| `validator` | — | `claude-haiku-4-5-20251001` | Conservative JSON gatekeeper; 5× cheaper than Sonnet |
| `research` | — | `gemini-2.5-pro` | 1M context; quantitative data analysis; no latency pressure |
| `param_suggest` | *(new role)* | `deepseek-reasoner` | Cheap reasoning model; step-by-step param-change inference |
| `regime` | *(new role)* | `gemini-2.5-flash-lite` | Free; runs hourly; short prompt |
| `fallback` | — | `grok-3-mini` | 131K context; structured output; low cost |

New roles added to `ROLES` tuple: `param_suggest`, `regime`.

**Model catalog updates:**
- `gemini-2.5-flash-lite`: added `"regime"` to roles
- `deepseek-reasoner`: already had `"param_suggest"` in roles (confirmed)
- `gemini-2.5-pro`: already had `"param_suggest"` in roles (confirmed)
- `qwen2.5:7b` (Ollama): `enabled=True`
- `qwen2.5:32b` (Ollama): `enabled=True`

**`backtester/asset_trainer.py` — `create_strategy_version()` default `ai_models`:**
```
"signal":        "gemini-2.5-flash-lite"
"validator":     "claude-haiku-4-5-20251001"
"research":      "gemini-2.5-pro"
"param_suggest": "deepseek-reasoner"
"regime":        "gemini-2.5-flash-lite"
"fallback":      "grok-3-mini"
```

**`core/config.py`:** `signal_model` default updated to `gemini-2.5-flash-lite`.

---

## 2026-03-31 — Strategy Card Model Override — All 6 Roles

### Summary
The AI Models panel on the Strategy Card now exposes all 6 model roles. The
dropdown is filtered to only show models whose API key is currently configured.

### Backend — `webui/routes/strategies.py`
`PUT /api/strategies/{symbol}/v{version}/models` now accepts all 6 role fields:
`signal`, `validator`, `research`, `param_suggest`, `regime`, `fallback`
(previously handled only 4 roles).

### Frontend — `webui/static/js/components/strategies.js`
AI Models panel renders one row per role with human-readable labels:

| Role key | UI label |
|----------|----------|
| `signal` | Signal |
| `validator` | Validator |
| `research` | Research |
| `param_suggest` | Optimizer |
| `regime` | Regime |
| `fallback` | Fallback |

Dropdown population rules:
- Only models whose provider key is configured (from `GET /api/test/models`) are shown
- If no API keys are set: shows `"No API keys configured — add keys in Settings"`
- If a previously-selected model's key is later removed: selection shows `"(key not set)"` suffix

---

## 2026-03-31 — Model Catalog Filtered by Configured API Keys

### Summary
`GET /api/test/models` now only returns models from providers that have a valid
key (or reachable Ollama endpoint), making the Strategy Card dropdowns safe to
populate directly from this endpoint.

### Logic (`webui/routes/test_connections.py`)
1. For each cloud provider: check DB-stored key (decrypted) first, then AppConfig env key.
   A provider is "configured" if either source has a non-empty value after strip.
2. For Ollama: live-ping `{QWEN_LOCAL_BASE}/api/tags` with 2 s timeout.
   `QWEN_LOCAL_BASE` is read from DB setting first, then `api_cfg.qwen_local_base`, defaulting to `http://localhost:11434`.
3. Return list of models from configured providers only, including `roles[]` and `cost_tier` per model.

**Response shape:**
```json
{
  "models": [
    { "id": "gemini-2.5-flash-lite", "provider": "gemini",
      "display_name": "Gemini 2.5 Flash Lite", "roles": ["signal","regime"],
      "cost_tier": 0 }
  ]
}
```

---

## 2026-03-31 — Local LLM (Ollama) Settings Section Added

### Summary
Settings → API Keys tab now includes a dedicated LOCAL LLM (OLLAMA) section with
a base-URL field and a live Test Connection button.

### Changes

**`webui/static/js/components/settings.js`:**
- New section `{ title: 'Local LLM (Ollama)', color: '#22c55e', ... }` added to the
  API Keys tab schema
- Field: `QWEN_LOCAL_BASE` — Ollama Base URL, default `http://localhost:11434/v1`
- Test Action: calls `POST /api/test/ollama`, displays model list on success
- Cache-bust version bumped: `app.js?v=6.4` → `app.js?v=6.5` (in `index.html`)

---

## 2026-03-31 — all_tools List in asset_trainer.py Completed

### Summary
`create_strategy_version()` in `asset_trainer.py` previously built `tool_config` from
an incomplete `all_tools` list that was missing the three newest indicator guards.
New strategy versions now include all 18 tools.

### Before (15 tools in list)
`ema200_filter`, `tick_jump_guard`, and `liq_vacuum_guard` were absent. Any strategy
version produced by SeedLab or the asset trainer had those three keys missing from
`tools{}`, so the loader fell back to "disabled" for them on every run.

### After (18 tools, matching `_DEFAULT_ORDER`)
```
session_filter, news_filter, volatility_filter, dxy_filter, sentiment_filter,
risk_filter, ema200_filter, macd_filter, bollinger_filter, adx_filter,
volume_filter, swing_structure, tick_jump_guard, liq_vacuum_guard,
bos_guard, fvg_guard, vwap_guard, correlation_guard
```
Ordering matches `tools/registry.py:_DEFAULT_ORDER` (slots 1–18).

---

## 2026-03-31 — Live Thoughts, Signal Intelligence, MetaLoop AI Fix

### Summary
Three frontend gaps and one backend wiring bug fixed. Live Trading Monitor now shows
real-time pipeline narration and a meaningful signal state when no bot crossover is active.

### Bug Fixes

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `main.py:181` | MetaLoop created without `ai_callback` — ResearchAnalyzer had `ai_callback=None`, skipping all AI-driven strategy improvements | Added `ai_callback=ai_caller` to MetaLoop constructor |
| 2 | `live.js:433` | WebSocket listener checked `data.type === 'signal'` — no such event type exists (correct is `SignalGenerated`), so bot signals never updated the Live tab | Fixed to handle `SignalGenerated`, `PipelineStep`, `CycleStarted`, `CycleCompleted` |
| 3 | `live.js:746` | Last Signal time showed "Invalid Date" — always multiplied timestamp by 1000, but WebSocket events send ISO strings not Unix seconds | Now detects type: `typeof ts === 'number' ? ts * 1000 : ts` |

### New Features

**Live Thoughts panel** (`#live-thoughts` div was empty since launch):
- `appendThought(stage, status, detail, timestamp)` function added to `live.js`
- Populated in real-time from `PipelineStep`, `CycleStarted`, `CycleCompleted` WebSocket events
- Stage icons: 🔄 cycle · 🛡 risk_check · 🔍 filters · 📡 signal_gen · ✅ validation · 🏰 guards · 📐 sizing · ⚡ execution
- Color-coded status: green = passed/filled, red = blocked/rejected, amber = no_signal
- Max 20 entries, newest first, "Waiting for bot events..." placeholder when empty
- CSS: max-height 80px → 160px, `.thought-line` separator styles

**Signal Intelligence scanning state** (was blank "AWAITING SIGNAL"):
- API now always returns `ema_state: { ema9, ema21, ema50, rsi, gap_pct, regime }` regardless of crossover signal
- When no active crossover: gauge shows **BUY / SELL / NEUTRAL** derived from EMA9/EMA21 gap direction
  - `gap > +0.05%` → BUY (green), `gap < -0.05%` → SELL (red), near-zero → NEUTRAL (amber)
- Label shows "EMA BIAS" to distinguish from a real crossover signal
- Bot signal persistence: `SignalGenerated` from bot sets 5-min lock preventing yfinance poll from overwriting with SCANNING

**Agent Status line** updated on `CycleCompleted` events — shows "Cycle #N — outcome" in real-time.

### Files Changed
- `src/alphaloop/main.py` — MetaLoop `ai_callback` (1 line)
- `src/alphaloop/webui/routes/live.py` — `ema_state` added to API response
- `src/alphaloop/webui/static/js/components/live.js` — `onWsEvent()`, `appendThought()`, signal panel scanning state, timestamp fix, bot signal persistence

---

## 2026-03-31 — Full System Audit: Wiring, Bugs, and Pipeline Visibility

### Context
Comprehensive audit of all modules revealed 8 unused-but-ready components, 3 critical
missing DI injections in `main.py`, 8 runtime bugs in `loop.py`, and empty context fields
that made 3 filter tools non-functional.

### P0 — Critical DI Wiring (main.py)

| Injection | What was broken | Impact |
|-----------|----------------|--------|
| `session_factory` → TradingLoop | Trades never logged to DB; guards couldn't query open positions; cross-instance risk broken | Per-cycle session pattern added |
| `ai_caller` (AICaller) → TradingLoop | AI signal generation always returned None; validator stage 2 auto-approved everything | Now routes to correct provider |
| `notifier` (TelegramNotifier) → TradingLoop | No Telegram alerts for trades/rejections | Reads creds dynamically from settings |

### P1 — Unused Components Now Wired

| Component | File | What it does |
|-----------|------|-------------|
| TradeRepositioner | `risk/repositioner.py` | Evaluates open trades each cycle; tightens SL / partial-close / full-close on triggers |
| OrderTracker + OrderRegistry | `execution/order_state.py` | State machine tracking order lifecycle inside MT5Executor |
| AlertEngine (5 rules) | `monitoring/alert_rules.py` | Configurable alerts with cooldowns; subscribed to TradeClosed + RiskLimitHit events |
| NotificationDispatcher | `notifications/dispatcher.py` | 60s batch + 300s dedup wrapper; prevents Telegram spam |
| metrics_tracker | `monitoring/metrics.py` | Records `cycle_duration_ms` and `signal_latency_ms` to Prometheus ring buffer |

New API route: `GET /api/alerts` — view/acknowledge alerts (web-only returns empty; bot process populates).

### P1.6 — Granular Pipeline Events (PipelineStep)

Added `PipelineStep` event type published at every checkpoint in the cycle (risk_check,
filters, signal_gen, validation, guards, sizing, execution). Each step has `stage`, `status`
(passed/blocked/no_signal/generated/etc), and `detail`. Frontend renders with color-coded
status badges (green=pass, red=block, muted=no_signal).

**Before:** Event Stream showed only `CycleStarted` → `CycleCompleted(no_signal)`
**After:** Full pipeline flow visible per cycle with live data at each stage.

### P2 — Context Pipeline Connected End-to-End

Wired 3 orphaned data fetchers into `_build_context()` with parallel async calls:

| Fetcher | Source | Context field | Cache TTL |
|---------|--------|--------------|-----------|
| `data/news.py` | FMP economic calendar API | `upcoming_news`, `news` | 5 min |
| `data/dxy.py` | yfinance DX-Y.NYB | `dxy` | 5 min |
| `data/polymarket.py` | Polymarket Gamma API | `sentiment`, `macro_sentiment` | 60 min |

This makes `news_filter`, `dxy_filter`, and `sentiment_filter` tools functional
(previously always saw empty data).

### Bug Fixes

| Bug | File | Issue | Fix |
|-----|------|-------|-----|
| `close_order()` | loop.py | Method doesn't exist | → `close_position()` |
| `modify_order()` | loop.py | Method doesn't exist | → `modify_sl_tp(ticket, sl, tp)` |
| `get_balance()` | loop.py | Method doesn't exist | → `get_account_balance()` |
| `confidence=` | loop.py | TradeLog field is `qwen_confidence` | Fixed |
| `risk_score=` | loop.py | TradeLog field is `claude_risk_score` | Fixed |
| `signal_reasoning=` | loop.py | TradeLog field is `claude_reasoning` | Fixed |
| `order_ticket=` | loop.py | Field doesn't exist in TradeLog | Replaced with `execution_price` + `slippage_points` |
| `get_current_price(symbol)` | loop.py | Passed unresolved symbol (XAUUSD vs XAUUSDm) → returned None → price=0 | Call without arg |
| M15 missing EMA/ATR | loop.py | `_build_context()` only computed RSI for M15; algo engine needs EMA21/EMA55/ATR | Added full computation |
| `size_mod` undefined | dxy_filter/tool.py | Variable referenced but never defined | Computed from DXY strength |
| `uuid` import | mt5_executor.py | Imported inside method | Moved to module level |
| Event count | main.py | Hardcoded `11`, actual count `14` | Fixed |
| Container attr | container.py | `alert_engine` dynamically added | Declared in `__init__` |

### Files Modified
`core/events.py`, `core/container.py`, `trading/loop.py`, `main.py`,
`execution/mt5_executor.py`, `db/repositories/trade_repo.py`,
`tools/plugins/dxy_filter/tool.py`, `webui/app.py`, `webui/routes/alerts.py` (new),
`webui/static/js/components/bots.js`, `monitoring/alert_rules.py` (wired),
`notifications/dispatcher.py` (wired), `risk/repositioner.py` (wired),
`execution/order_state.py` (wired), `data/news.py` (wired), `data/dxy.py` (wired),
`data/polymarket.py` (wired)

---

## 2026-03-30 — BUG: Raw Log Empty + Pipeline Status "waiting…" on Alpha Agent Cards

### Symptoms
- All 9 pipeline stage tiles in the Raw Signal Log modal showed `waiting…` indefinitely
- Event Stream section showed "No events yet — stream will update automatically."
- Running bot (`XAUUSD_93b338da`, PID alive) had zero entries in the `/api/events` ring buffer

### Root Cause 1: `CycleStarted` event never published (`trading/loop.py`)
`bots.js` expects a `CycleStarted` event for the **🔄 Cycle** pipeline tile, but the class existed
only as a planned item in `core/events.py` and was never actually published by the trading loop.
The `_cycle()` method incremented `_cycle_count` and immediately entered risk checks — no event fired.

**Fix:** Added `CycleStarted` dataclass to `core/events.py` and publish it at the very first line
of `_cycle()` before any early-return guard, so the Cycle tile always activates within the first
poll interval.

### Root Cause 2: Three silent early-return paths published no events (`trading/loop.py`)
If the bot was blocked by cross-instance risk, risk monitor, or circuit breaker, `_cycle()` returned
silently with only a `logger.info()`. Because `CycleStarted` also wasn't published, the Raw Log
received zero events — identical to a bot that had never run a single cycle.

**Fix:** Each silent `return` now publishes a `PipelineBlocked` event with `blocked_by` set to
`"cross_instance_risk"`, `"risk_monitor"`, or `"circuit_breaker"` respectively.

### Root Cause 3: Event bridge used blocking `urllib` inside `async def` (`main.py`)
`_bridge_event` called `urllib.request.urlopen(req, timeout=1)` synchronously, blocking the event
loop for up to 1 second per event. Failures were logged at `DEBUG` level (invisible by default),
so any connection issue silently dropped events with no trace in normal logs.

**Fix:**
- Extracted the HTTP POST into `_do_post(payload)` (sync function)
- Offloaded via `await asyncio.to_thread(_do_post, payload)` — non-blocking
- Upgraded failure logging from `DEBUG` → `WARNING`
- Added `CycleStarted` to the bridge subscription list (11 event types, was 10)

**Files modified:** `core/events.py`, `trading/loop.py`, `main.py`

---

## 2026-03-30 — Live Page: Real Signal Intelligence + yfinance Period Fix

### BUG: `GC=F` (Gold Futures) returned no data with `period=1d`

**Root cause:** yfinance does not return minute-level data for front-month futures contracts
when `period=1d`. `_YF_PERIOD["1m"]` was `"1d"` — yfinance responded with
`"possibly delisted; no price data found"`, the exception was silently swallowed,
and the API returned `price: null, ohlc: []` for XAUUSD.

**Fix (`routes/live.py:26`):** Changed `_YF_PERIOD["1m"]` from `"1d"` to `"5d"`.
`GC=F` with `period=5d, interval=1m` returns 5,000+ bars correctly.

### Signal Intelligence panel now shows computed data

**Problem:** `signal`, `market_regime`, and `recent_signals` were hardcoded `null`/`unknown`/`[]`.
The Signal Intelligence card showed nothing for any symbol.

**Fix (`routes/live.py` — `_fetch_ohlc_sync`):** Added inline EMA/RSI computation:
- **EMA-9, EMA-21, EMA-50** via exponential moving average over all fetched bars
- **RSI-14** from the last 15 closes
- **Market regime:** `trending_up` (9>21>50), `trending_down` (9<21<50), `ranging` (mixed)
- **Signal:** EMA-9/21 crossover or RSI extreme (< 35 / > 65), confidence 0.55–0.95
- **Recent signals:** last 5 EMA crossovers from trailing 50 bars (timestamp + price)

**Fix (`live.js` — `updateSignalPanel`):** Now accepts the full API response object and
updates three previously-dead DOM elements:
- `#live-regime` — "▲ Trending Up", "▼ Trending Down", "↔ Ranging" with color
- `#live-recent` — timestamped BUY/SELL crossover pills
- `#live-sig-time` — timestamp of the last active signal

**Files modified:** `routes/live.py`, `static/js/components/live.js`

---

## 2026-03-30 — Responsive CSS Audit + Hamburger Menu

- **Design tokens** added to `:root`: `--sp-*`, `--fs-*`, `--t-*`, `--r-*`, `--sidebar-w`, `--hamburger-size`
- **5-breakpoint system** (1441/1024/768/480px) replacing single legacy media query
- **Hamburger toggle:** `.hamburger-btn` + `.sidebar-overlay` in HTML; `body.sidebar-open` drives state
- **`initSidebar()` in `app.js`:** click, overlay tap, Escape, nav-item, and resize all close sidebar
- **Z-index hierarchy:** toast→600, symbol-dropdown→500, strat-overlay→400, strat-panel→401
- **Event Log table:** wrapped in `.table-scroll-wrap` for mobile horizontal scroll

**Files modified:** `app.css`, `index.html`, `app.js`, `event_log.js`

---

## 2026-03-30 — SeedLab Pause/Resume Fix + Color Log System

**Bug fixes in `backtester/runner.py`:**

- **BUG: Checkpoint always discarded on resume** — `_load_checkpoint` did a hard `return None` on data hash mismatch. Since yfinance fetches the last N days each time, the window shifts on every resume → hash never matched → run always restarted from gen 1. Fix: hash mismatch now logs `[WARN]` and falls through to load params anyway (checkpoint is keyed by unique `run_id`, no wrong-run risk).
- **BUG: `was_stopped` branch had no checkpoint save** — when `optimize()` returned early due to stop flag, code called `update_state("paused")` but never saved a checkpoint or updated DB stats (`best_wr`, `best_pnl`, `best_dd`). Fix: now saves checkpoint at `gen-1` and calls `update_progress` with full stats before transitioning to paused.
- **BUG: Stop at gen boundary missing `update_progress`** — gen-loop stop check called `update_state("paused")` without a prior `update_progress`, leaving stat cards stale. Fix: `update_progress` is always called before `update_state` on both stop paths.
- **BUG: Log history wiped on resume** — `start_backtest` reset `_logs[run_id] = []` on every call (including resume). Fix: if prior log buffer exists, appends a `─── Resumed ───` separator and preserves all previous output.

**Color-categorized log system:**

- `_log(run_id, msg, level="INFO")` gains optional `level` param. Default `"INFO"` emits no prefix (backward compat). All others emit `[LEVEL]` prefix in the buffer.
- Log levels: `GEN` (generation start/end/completion), `STAT` (stats/results), `DATA` (data fetch/split), `CKPT` (checkpoint save/load), `WARN` (overfit, early stop, fallbacks), `ERR` (fatal).
- `_save_checkpoint` and `_load_checkpoint` now emit `[CKPT]` log lines visible in Live Output.
- All `_log` calls throughout `_run_backtest` and `_fetch_data_*` tagged with appropriate levels.

**Frontend log viewer (`backtests.js`):**

- `colorize(line)` helper: HTML-escapes each line then wraps in a `<span class="log-{level}">` based on tag match.
- CSS injected once per page load (guarded by `#bt-log-colors` id): `log-gen`=blue, `log-stat`=green, `log-ckpt`=amber bold, `log-warn`=amber, `log-err`=red, `log-data`=#64b5f6, `log-info`=muted.
- `fetchLogs` switched from `textContent +=` to `innerHTML +=` with `colorize` mapping, making log output color-coded in the browser.

**Files modified:** `backtester/runner.py`, `webui/static/js/components/backtests.js`

---

## 2026-03-30 — Sound System, Improved Signal Log Modal, Sounds Settings Tab

**New file: `static/js/sounds.js` — Web Audio API synthesizer (no audio files):**
- Pure JS tones using AudioContext + OscillatorNode; context created lazily on first user gesture (browser autoplay policy compliant)
- Five named sounds: `playTradeOpened()` (E5→G#5 ping), `playTradeClosedProfit()` (E5→G5→C6 ascending trio), `playTradeClosedLoss()` (G4→E4→C4 descending trio), `playSeedLabDone()` (C5→E5→G5→C6 fanfare), `playEvolution()` (C5→E5→G5→B5→C6 arpeggio)
- Each sound guarded by a `localStorage` key (`sounds_enabled`, `sounds_trade_open`, etc.)
- Exports: `isGloballyEnabled()`, `getVolume()`, `isEventEnabled(key)`, `setSoundsEnabled()`, `setVolume()`, `setEventEnabled(key, val)` — all preferences persist to `localStorage`

**`bots.js` — sound triggers wired into `handleWSEvent`:**
- `TradeOpened` → `playTradeOpened()`
- `TradeClosed` → `playTradeClosedProfit()` if `data.pnl_usd >= 0`, else `playTradeClosedLoss()`
- `StrategyPromoted` → `playEvolution()`
- Added missing `TradeClosed` switch case (also resets `st.lastTrade = null`)

**`backtests.js` — sound trigger on completion:**
- State transition to `'completed'` → `playSeedLabDone()`

**`bots.js` — Raw Signal Log modal redesigned:**
- Old: single `rawLogBody` div, "No events yet" or raw list, raw `fetch()` (auth bug)
- New two-section layout:
  - **Pipeline Status Grid**: 3-column grid of 9 stage cards (CycleStarted, SignalGenerated, SignalValidated, SignalRejected, TradeOpened, TradeClosed, PipelineBlocked, RiskLimitHit, StrategyPromoted). Every card always rendered — shows icon, label, last event time, and key detail snippet; dimmed ("waiting…") until that event type arrives
  - **Event Stream**: chronological raw event list below the grid
- Auto-polls `GET /api/events` every 3 seconds while modal is open; `🟢 live` indicator shown in header
- Polling stops on modal close (backdrop click or ✕ button)
- Fixed: `loadRawLog()` now uses `apiGet()` from `../api.js` (adds auth header, proper error handling)

**`settings.js` — new "Sounds" tab:**
- Added `{ id: 'sounds', label: 'Sounds', icon: '🔊', localOnly: true }` to SCHEMA
- `localOnly: true` tabs skip the server `PUT /api/settings` call and hide the "Save Changes" footer button
- `renderSoundsPanel()` renders two sections:
  - **Master Controls**: global Sound Effects toggle + Volume slider (0–100%)
  - **Event Sounds**: 5 rows (Trade Opened, Trade Closed—Profit, Trade Closed—Loss, SeedLab Complete, Strategy Evolution) each with icon, name, description, sound notation, ▶ Preview button (plays regardless of toggle for auditioning), and On/Off toggle
- All changes persist instantly to `localStorage`; no server save required

**`event_log.py` — note:** `/api/events` and `/api/events/ingest` already existed; the raw `fetch()` bug in `loadRawLog` was masking auth errors from the endpoint.

**Files Modified:** sounds.js (new), bots.js, backtests.js, settings.js

---

## 2026-03-30 — Signal Mode, Event Bridge, MT5 Live Context

**Signal Mode Selector:**
- Moved signal mode toggle from SeedLab to Strategy Cards (per-strategy config, not backtest config)
- Two modes: Algo Only (no AI cost) and Algo+AI (algo fires, AI gate-keeps). Dropped AI Only.
- Clicking Algo Only hides AI Models row; Algo+AI shows it. Saves immediately via `PUT /api/strategies/{sym}/v{ver}/models`
- Backend: `strategies.py` extended to accept+persist `signal_mode` in strategy JSON
- Frontend: `backtests.js` signal mode toggle removed from SeedLab

**Alpha Agent Card Redesign:**
- Header shows strategy name + V1 badge (extracted from `_v1` suffix in SeedLab name), not just symbol
- `stratBaseName()` strips `_v1` suffix, `stratVersionBadge()` renders separate pill — auto-updates on evolution
- Uptime counter starts from 0:00 on page load, ticks every second via `setInterval`
- Removed redundant "Version" stat and "Started at" footer
- Loop Status section hidden until first WebSocket event arrives
- Raw Signal Log button opens per-instance modal showing event history

**Event Bridge (subprocess → web server):**
- Trading agents run as subprocesses with isolated event buses — events never crossed process boundaries
- Added `POST /api/events/ingest` endpoint on web server (ring buffer, max 200 events)
- Added `GET /api/events` filters: `?symbol=`, `?instance_id=` for per-agent isolation
- Bridge subscriber in `main.py` POSTs each event (sync, <50ms) to ingest endpoint
- Subscribes to 10 event types individually (PipelineBlocked, SignalGenerated, etc.)

**EventBus MRO Fix:**
- `EventBus.publish()` used exact type matching — subscribing to `Event` base class missed all subclasses
- Fixed: traverses `__mro__` so base class subscription catches all derived events

**Pipeline Key Fix:**
- `loop.py` checked `pipeline_result.get("blocked")` but pipeline returns `"allow_trade": False`
- Fixed both strategy pipeline and overlay pipeline checks to use `not pipeline_result.get("allow_trade", True)`

**Context Fix (AttrDict):**
- `_build_context()` returned a `dict` but all tools use attribute access (`context.session`)
- Changed to `AttrDict` (dict subclass with `__getattr__`) — works for both `context.session` (tools) and `context.get("timeframes")` (algo engine)

**MT5 Live Data:**
- MT5 now connects in dry run mode (was skipped before) — needed for price/candle data
- Auto-resolves broker symbol suffix: `XAUUSD → XAUUSDm` (Exness), tries `m`, `M`, `.raw` fallbacks
- `_build_context()` fetches real H1/M15 candles from MT5 synchronously (avoids thread-unsafe `asyncio.to_thread`)
- Computes ATR (14-period), EMA 21/55, RSI (14) from live candle data
- `get_session_info()` now includes `is_weekend` field

**Strategy Card Picker (Alpha Agents deploy modal):**
- Fixed CSS overflow: `.form-group input { width: 100% }` was making radio inputs 913px wide
- Added `width:auto;flex-shrink:0` on radio inputs, `overflow-x:hidden` on picker container
- Strategy name truncated with `text-overflow:ellipsis;max-width:180px`

**Files Modified:** loop.py, events.py, main.py, event_log.py, time.py, fetcher.py, mt5_executor.py, strategies.py, strategies.js, backtests.js, bots.js

---

## 2026-03-29 — G.4-G.9 New Features Complete

**241 tests passing, 0 failures.**

### New Backend Modules (6 files)
- `trading/portfolio_manager.py` — Multi-symbol portfolio coordination (max positions, heat cap, cross-symbol tracking)
- `data/live_feed.py` — Real-time price streaming (MT5 primary, yfinance fallback, tick caching)
- `research/report_generator.py` — Automated daily/weekly/monthly performance reports with Telegram formatting
- `monitoring/alert_rules.py` — Configurable alert rules engine (5 default rules: daily loss, consecutive losses, portfolio heat, circuit breaker, spread spike)
- `core/feature_flags.py` — Runtime feature toggle system (10 default flags, DB sync, runtime overrides)
- `webui/routes/event_log.py` + `webui/routes/risk_dashboard.py` — New API routes (created earlier)

### New Frontend Pages (3 files)
- `components/health.js` — System health dashboard (component status cards, watchdog info, auto-refresh 15s)
- `components/event_log.js` — Live event log viewer (type filter badges, auto-refresh 5s)
- `components/risk_dashboard.js` — Risk monitor dashboard (daily P&L, consecutive losses, win rate bar, heat meter)

### Navigation Updates
- Added "Risk & Monitoring" nav group with Risk, Event Log, Health pages
- SPA router updated for 3 new routes

### WebSocket Event Handlers (app.js)
- Central handler for all 13 event types with toast notifications
- Toast CSS: added `info` and `warning` border colors

### Test Coverage
- 4 new test files: test_portfolio_manager.py (6), test_feature_flags.py (5), test_alert_rules.py (6), test_report_generator.py (3)
- Total: 241 tests (up from 221)

---

## 2026-03-29 — Full Audit Fixes Complete (67 fixes, 50+ files)

All CRITICAL (5), HIGH (21), MEDIUM (44) audit items fixed. 7 new test files added (55 tests).

Key fixes:
- CLI entry point, SIGTERM Windows, missing methods, asyncio patterns
- Trading guards fixed (getattr-on-dict, circuit breaker kill, spread data)
- Live page wired with yfinance data + EMA/RSI chart overlays
- Settings tab data persistence, WebSocket subscription leak
- AI Hub role key alignment, signal_mode in backtests
- Backtester ATR alignment, TP1/TP2 order, session filter timestamps
- Schema drift fixed (String widths + missing columns)
- Monte Carlo thread offloaded, API error bodies preserved
- 15 unused imports removed, dead code cleaned up

---

## 2026-03-29 — Full Codebase Audit

**Auditor:** Claude Opus 4.6
**Scope:** 188 Python files, 14 JS files, 1 CSS file, tests

| Severity | Count |
|----------|-------|
| CRITICAL | 5 |
| HIGH | 21 |
| MEDIUM | 44 |
| LOW | 80+ |

### CRIT-01: Entry point references non-existent `cli` function
- `pyproject.toml:57` — `alphaloop.main:cli` but `main.py` has no `cli()` function
- **Fix:** Create `cli()` wrapper or change entry point to `alphaloop.main:main`

### CRIT-02: `signal.SIGTERM` crashes on Windows
- `main.py:92` — Windows does not support `SIGTERM` via `signal.signal()`
- **Fix:** Wrap in `try/except OSError` or platform check

### CRIT-03: `repo.get_closed()` does not exist
- `research/analyzer.py:341` — `TradeRepository` only has `get_closed_trades()`
- **Fix:** Change to `repo.get_closed_trades()`

### CRIT-04: `asyncio.run()` inside running event loop
- `backtester/asset_trainer.py:268` — nested `asyncio.run()` raises `RuntimeError`
- **Fix:** Use `await` directly or `asyncio.to_thread()`

### CRIT-05: Migration script uses wrong column names
- `scripts/migrate_v2_db.py:81-93` — INSERT references columns not in v3 schema
- **Fix:** Rewrite to match v3 column names

### Key Audit Findings
- Trading guards silently disabled (`getattr()` on dicts always returns default)
- Live Trading Monitor returns hardcoded None/empty for all data
- Settings page loses edits when switching tabs before saving
- WebSocket event handler leaks on reconnection
- Backtester ATR computed with off-by-one alignment error
- Schema drift: ORM models define `String(32)` but migration creates `String(16)`

Full audit: see `AUDIT_AND_IMPLEMENTATION_PLAN.md` (archived)

---

## 2026-03-29 — BUG-001: Backtest Infinite Poll (FIXED)

**Symptom:** Backtest page stuck, hammering `/api/backtests` every second.
**Root cause:** Backtest process killed mid-run without updating DB state. Row stayed at `state="running"` forever. UI polls while `state == "running"`.
**Fix:** `PATCH /api/backtests/{run_id}/stop` now force-sets `state = "paused"` when `request_stop()` returns False (task not in memory).
**Prevention:** On server startup, mark stale `state="running"` rows as `"paused"`.

---

## 2026-03-29 — BUG-002: Resume Restarts Backtest from Scratch (FIXED)

**Symptom:** Stopping a backtest mid-run and resuming restarts from generation 1.
**Root cause:** `resume_backtest` didn't pass `timeframe` or `tools` — both defaulted wrong — causing `data_hash` mismatch — checkpoint discarded.
**Secondary:** `tools_json` was never saved to DB on creation.
**Fix:**
- `create_backtest` now saves `tools_json` to DB
- `resume_backtest` reads `run.timeframe` and `run.tools_json` from DB and passes them

---

## 2026-03-28 — WebUI Makeover Complete

- Primary color: `#EF9F27` (gold), light mode variant `#d48b1a`
- Dark + light mode toggle (localStorage, no DB round-trip)
- 10-tab Flaticon+emoji navigation in 4 groups
- Live Trading Monitor with Lightweight Charts v4.2 (ported from v1)
- SeedLab rename with signal mode toggle
- Alpha Agents with WebUI Deploy/Stop subprocess management
- Strategy status pill tabs with lifecycle visualization
- 22+ missing setting defaults seeded on startup
- Agent registration bug fixed
- 20 files modified/created

---

## 2026-03-20 — Phases 0-8 Complete

All core phases of the v3 rewrite completed:
- Phase 0: Scaffolding (config, DB, health endpoint)
- Phase 1: Domain models & schemas (7 DB models, repositories)
- Phase 2: AI layer (4 providers: Anthropic, Gemini, OpenAI-compat, Ollama)
- Phase 3: Data layer + tool plugins (10 tools, plugin auto-discovery)
- Phase 4: Signal + validation + risk (13 hard rules, 7 risk guards)
- Phase 5: Trading loop (strategy loader, meta-loop, health monitor, micro-learner)
- Phase 6: WebUI (14 route modules, 10-tab SPA, WebSocket live updates)
- Phase 7: Research + SeedLab + Backtester (Optuna optimization, deployment pipeline)
- Phase 8: Monitoring (structlog, health checks, watchdog)

Phase 9 (Testing + Hardening) in progress.
