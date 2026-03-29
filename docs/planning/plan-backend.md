# AlphaLoop v3 — Backend Architecture

## Stack
- Python 3.11+, async-first (asyncio throughout)
- FastAPI + Uvicorn (WebUI/API)
- SQLAlchemy 2.0 async (aiosqlite / asyncpg)
- Pydantic v2 (config, schemas, validation)
- MetaTrader5 API (execution, wrapped via asyncio.to_thread)

## Module Map

```
src/alphaloop/
├── __init__.py              (4 lines)    version = "3.0.0"
├── app.py                   (40 lines)   create_app() → Container
├── main.py                  (145 lines)  CLI entry point, argparse, signal handlers
│
├── core/
│   ├── config.py            (195 lines)  AppConfig(BaseSettings): Broker, Risk, Signal, Session, API, Evolution, DB, Telegram
│   ├── container.py         (45 lines)   Container: config, event_bus, db_engine, db_session_factory
│   ├── constants.py         (72 lines)   RISK_HARD_CAPS, POLL_INTERVAL_SEC=300, circuit breaker thresholds
│   ├── events.py            (155 lines)  EventBus + 15 event types (SignalGenerated, TradeOpened, TradeClosed, StrategyPromoted, SeedLabProgress, CanaryStarted, CanaryEnded, MetaLoopCompleted, StrategyRolledBack, etc.)
│   ├── errors.py            (38 lines)   8 exception classes (ConfigError, SignalError, ExecutionError, etc.)
│   ├── lifecycle.py         (35 lines)   startup(container), shutdown(container); seeds SETTING_DEFAULTS into DB on startup (Signal 25 + Tools 43 + MetaLoop 5 + Health 6 + Confidence 1 + Micro 3 = 83+ keys)
│   └── types.py             (86 lines)   11 StrEnum types (TradeDirection, SetupType, AIProvider, etc.)
│
├── db/
│   ├── engine.py            (56 lines)   create_db_engine() — SQLite WAL + PostgreSQL pool
│   ├── session.py           (35 lines)   create_session_factory(), get_session() context manager
│   ├── models/
│   │   ├── base.py          (25 lines)   Base(DeclarativeBase), TimestampMixin
│   │   ├── trade.py         (125 lines)  TradeLog (100+ columns, 6 indexes), TradeAuditLog
│   │   ├── backtest.py      (77 lines)   BacktestRun (state machine: pending→running→completed/failed)
│   │   ├── settings.py      (26 lines)   AppSetting (key/value store)
│   │   ├── research.py      (80 lines)   ResearchReport, ParameterSnapshot, EvolutionEvent
│   │   ├── instance.py      (28 lines)   RunningInstance (multi-instance collision guard)
│   │   └── pipeline.py      (56 lines)   PipelineDecision, RejectionLog
│   ├── repositories/
│   │   ├── trade_repo.py    (83 lines)   TradeRepository: create, get_open, get_closed, count_by_outcome
│   │   ├── backtest_repo.py (86 lines)   BacktestRepository: create, get_by_run_id, update_state/progress
│   │   ├── research_repo.py (73 lines)   ResearchRepository: reports, snapshots, evolution events
│   │   └── settings_repo.py (44 lines)   SettingsRepository: get, get_all, set, set_many, delete
│   └── migrations/
│       └── env.py                        Alembic async migration env (uses Base.metadata)
│
├── ai/
│   ├── model_hub.py         (361 lines)  25+ BUILTIN_MODELS, ModelConfig, role resolution, provider key mapping
│   ├── caller.py            (228 lines)  AICaller: call_model(), call_role(), _dispatch() to providers
│   ├── rate_limiter.py      (84 lines)   AsyncRateLimiter: sliding window per provider (10 calls/min default)
│   └── providers/
│       ├── anthropic.py     (106 lines)  AnthropicProvider: httpx async to Claude API
│       ├── gemini.py        (132 lines)  GeminiProvider: google.generativeai SDK
│       ├── openai_compat.py (136 lines)  OpenAICompatProvider: OpenAI/DeepSeek/xAI/Qwen via /v1/chat/completions
│       └── ollama.py        (60 lines)   OllamaProvider: local Ollama via OpenAI-compat endpoint
│
├── signals/
│   ├── schema.py            (164 lines)  TradeSignal, ValidatedSignal, RejectionFeedback — Pydantic v2 with injection detection
│   ├── engine.py            (153 lines)  MultiAssetSignalEngine: generate_signal(strategy_params), prompt building, JSON parsing
│   └── algorithmic.py       (135 lines)  AlgorithmicSignalEngine: deterministic EMA+RSI signal gen for Mode A/B, reads from context dict
│
├── validation/
│   ├── rules.py             (182 lines)  HardRuleChecker: 9 checks (confidence, SL/TP, RR, session, spread, RSI, EMA200, news)
│   └── validator.py         (254 lines)  UniversalValidator: hard rules → optional AI validation → ValidatedSignal; accepts validation_overrides from active strategy
│
├── trading/
│   ├── loop.py              (450+ lines) TradingLoop: strategy loading → pipeline → overlay → signal mode branching → validate → guards → size → execute → TradeClosed publish
│   ├── strategy_loader.py   (100 lines)  ActiveStrategyConfig, load_active_strategy(), build_strategy_pipeline() — reads active_strategy_{symbol} from DB
│   ├── overlay_loader.py    (90 lines)   DryRunOverlayConfig, load_overlay_config(), build_overlay_pipeline() — per-card dry-run overlay
│   ├── meta_loop.py         (220 lines)  MetaLoop (TradeClosed event-driven, asyncio.Task) + RollbackTracker (R-multiple Sharpe)
│   ├── health_monitor.py    (130 lines)  StrategyHealthMonitor: composite score (sharpe+winrate-drawdown-stagnation), HealthStatus enum
│   ├── micro_learner.py     (175 lines)  MicroLearner: per-trade param nudges (±1% per trade, ±5% drift cap), SL/confidence recalibration
│   ├── circuit_breaker.py   (63 lines)   CircuitBreaker: failure tracking, is_open, should_kill
│   └── heartbeat.py         (34 lines)   HeartbeatWriter: periodic JSON file for external monitoring
│
├── risk/
│   ├── sizer.py             (182 lines)  PositionSizer: ATR-based sizing, confidence-based multiplier (0.85+→1.25×, <0.55→0.5×), risk_pct_override, margin cap, macro modifier, vol regime
│   ├── monitor.py           (216 lines)  RiskMonitor: daily loss, consecutive losses, session caps, portfolio heat, kill switch
│   ├── guards.py            (250+ lines) 7 stateful guards (all real implementations):
│   │                                      SignalHashFilter (dedup window=3), ConfidenceVarianceFilter (window=3, max_stdev=0.15),
│   │                                      SpreadRegimeFilter (window=50, threshold=1.8×), EquityCurveScaler (window=20, scale=0.5×),
│   │                                      DrawdownPauseGuard (pause=30min, lookback=3 losses),
│   │                                      NearDedupGuard (min_atr_distance=1.0), PortfolioCapGuard (max_risk_pct=6.0%)
│   └── repositioner.py      (174 lines)  TradeRepositioner: 4 triggers — opposite_signal (full close), news_risk (SL→BE/partial, 15min window),
│                                          volume_spike (SL→BE if profit, 2.5× 20-bar avg), volatility_spike (SL→BE if profit, 1.8× ATR baseline)
│
├── execution/
│   └── mt5_executor.py      (324 lines)  MT5Executor: async connect, open_order, close_order, get_balance, dry-run mode
│
├── data/
│   ├── fetcher.py           (219 lines)  OHLCVFetcher: MT5 primary, yfinance fallback, per-timeframe TTL cache. Uses yf_catalog for ticker mapping.
│   ├── yf_catalog.py        (180 lines)  116 yfinance symbols across 13 asset classes. SYMBOL_TO_YF map, get_catalog_for_api().
│   ├── market_context.py    (242 lines)  MarketContext: aggregates OHLCV + indicators + news + session + DXY + sentiment
│   ├── indicators.py        (320 lines)  atr(), ema(), rsi(), vwap(), detect_bos(), detect_fvg(), find_swing_highs_lows(), macd(), bollinger(), adx(), volume_ma()
│   ├── dxy.py                            DXY index fetcher for gold correlation
│   ├── news.py                           Economic calendar / high-impact news events
│   └── polymarket.py        (169 lines)  Prediction market sentiment data
│
├── config/
│   ├── assets.py            (266 lines)  AssetConfig per symbol (XAUUSD, BTCUSD, forex, indices), get_asset_config()
│   ├── settings_service.py  (285 lines)  SettingsService: async KV store with 60s TTL cache + seed_defaults() + SETTING_DEFAULTS (83+ keys: 25 Signal + 43 Tools + 5 MetaLoop + 6 Health + 1 Confidence + 3 Micro, seeded on startup for absent/empty keys)
│   └── strategy_params.py                Strategy parameter tuning config
│
├── tools/
│   ├── pipeline.py          (100 lines)  FilterPipeline: sequential tool execution, short-circuit on block, crash recovery
│   ├── base.py                           BaseTool abstract, ToolResult model (passed, reason, severity, size_modifier, bias, latency_ms)
│   ├── registry.py                       Tool registration and discovery
│   └── plugins/                          10 pipeline filter tools (all real implementations):
│       ├── session_filter/               Order 1: Tradeable session check (overlap=1.0 > london=0.80 > ny=0.85 > asia=0.20; blocks weekend)
│       ├── news_filter/                  Order 2: High-impact news blackout (pre=30min, post=15min; blocks HIGH/CRITICAL events)
│       ├── volatility_filter/            Order 3: ATR range check (block if >2.5% or <0.05%; soft reduce at >2.0%)
│       ├── dxy_filter/                   Order 4: USD inverse correlation (gold BUY blocked when USD bullish; 60min cache)
│       ├── sentiment_filter/             Order 5: Polymarket sentiment (never blocks; reduces size 50% on conflict; 60min cache)
│       ├── risk_filter/                  Order 6: Risk limits gate (delegates to RiskMonitor: daily loss, kill switch, max concurrent)
│       ├── bos_guard/                    Validation: Break of Structure (M15 close vs swing H/L; min_break_atr=0.2, lookback=20)
│       ├── fvg_guard/                    Validation: Fair Value Gap (entry inside gap zone; min_size_atr=0.15, lookback=20)
│       ├── vwap_guard/                   Validation: VWAP extension (block if >1.5 ATR from VWAP; fail-safe block if data missing)
│       └── correlation_guard/            Portfolio: Cross-pair correlation (block ≥0.90 same-dir, reduce ≥0.75; 12 static pair correlations)
│
├── backtester/
│   ├── engine.py            (402 lines)  BacktestTrade, BacktestResult, simulation engine, equity/sharpe/DD metrics
│   ├── runner.py            (650+ lines) Background task runner with Optuna optimization loop:
│   │                                      Gen 1: baseline with default params → Gen 2+: Optuna TPE (30 trials) on 80% train split →
│   │                                      validate on 20% holdout → reject if overfit gap > 0.30 → accept if Sharpe improves > 0.05
│   │                                      Signal fn: EMA crossover + RSI with tunable params (ema_fast/slow, sl_atr_mult, tp1/tp2_rr, rsi_ob/os,
│   │                                      macd_fast/slow/signal, bb_period/std_dev, adx_period/min_threshold, volume_ma_period)
│   │                                      + 13 backtest-compatible tool filters (session, volatility, ema200, bos, fvg, tick_jump, liq_vacuum, vwap, macd, bollinger, adx, volume, swing_structure). Multi-TF yfinance data (1m→1mo). Early stop after 2 no-improve gens.
│   │                                      Checkpoint save/load for resume after stop/crash. Auto-creates strategy version on completion.
│   ├── params.py            (45 lines)   BacktestParams Pydantic model: ema_fast=21, ema_slow=55, sl_atr_mult=2.0, tp1_rr=2.0, tp2_rr=4.0,
│   │                                      rsi_ob=70, rsi_os=30, macd_fast=12, macd_slow=26, macd_signal=9, bb_period=20, bb_std_dev=2.0,
│   │                                      adx_period=14, adx_min_threshold=20, volume_ma_period=20, risk_pct=0.01, max_param_change_pct=0.15
│   ├── optimizer.py         (150 lines)  Optuna TPE optimizer: suggest_params() (bounded ±15%), split_data() (80/20),
│   │                                      optimize() (30 trials, 5 startup random, seed=42). Prunes if tp1_rr<1.3 or sl_atr_mult<0.8
│   ├── parallel_backtest.py (158 lines)  Multi-process backtest scheduling
│   ├── auto_improve.py      (200 lines)  Bayesian parameter optimization
│   ├── asset_trainer.py     (280+ lines) SeedLab→Optuna→strategy version bridge. Trains from card, creates version JSON. ai_models schema: {signal, validator, research, autolearn, fallback} — per-strategy model assignment, editable from Strategies UI via PUT /api/strategies/{sym}/v{ver}/models.
│   └── deployment_pipeline.py (340 lines) candidate → dry_run → demo → live promotion with StageGate validation, Monte Carlo robustness check (Sharpe significance + ruin probability) for demo→live gates, canary deployment (start_canary/end_canary: reduced allocation for N hours, evaluate metrics, recommend promote/reject)
│
├── research/
│   ├── analyzer.py          (321 lines)  Trade analysis, per-setup/session/hourly breakdown
│   ├── evolution_guard.py   (188 lines)  Drift detection, max parameter change enforcement
│   ├── monte_carlo.py       (164 lines)  Monte Carlo simulation for strategy robustness
│   ├── applier.py                        Parameter changes application with rollback
│   └── prompts.py                        AI prompts for research analysis
│
├── seedlab/
│   ├── seed_generator.py    (221 lines)  12+ strategy seed templates, combinatorial generation, SHA256 hashing
│   ├── runner.py            (248 lines)  SeedLab pipeline: generate → backtest → score → rank → save
│   ├── regime_detector.py   (209 lines)  Market regime classification (trending/ranging/volatile)
│   ├── metrics.py           (197 lines)  Backtest metrics extraction
│   ├── ranking.py                        Composite scoring (WR × Sharpe × (1-DD) × stability)
│   ├── stability.py                      OOS validation, parameter sensitivity
│   ├── strategy_card.py                  StrategyCard model
│   └── registry.py                       Strategy card persistence
│
├── monitoring/
│   ├── metrics.py                        In-memory ring buffer (5min buckets, 24h history)
│   ├── logging.py                        Structured JSON logging
│   ├── health.py                         Health check aggregator (ComponentStatus: healthy/degraded/unhealthy/unknown)
│   └── watchdog.py                       Trading loop health watchdog — monitors heartbeat.json, detects stale/crashed loop, kill switch active, circuit breaker open. Auto-starts as asyncio.Task in webui lifespan. Publishes RiskLimitHit events on alert. Throttled: 1 alert per 5min. Config: check_interval=60s, stale_threshold=600s, critical_threshold=900s.
│
├── notifications/
│   ├── telegram.py                       Async Telegram alerts (trade opens/closes, risk limits, errors)
│   └── dispatcher.py                     Multi-channel notification routing
│
├── utils/
│   ├── time.py                           Session detection, UTC conversions
│   ├── crypto.py                         API key encryption (Fernet, v2-compatible key derivation), encrypt_value/decrypt_value for `enc::` prefixed ciphertext
│   └── credits.py                        API usage tracking
│
└── webui/                                (see plan-ui.md)
```

## Database Schema (6 Tables)

### trade_logs
100+ columns. Key fields:
- Identity: id (PK), signal_id, symbol, direction, instance_id
- Signal: setup_type, timeframe, confidence, signal_reasoning, signal_json (JSON)
- Entry: entry_price, entry_zone_low/high, lot_size, risk_pct, risk_amount_usd
- SL/TP: stop_loss, take_profit_1, take_profit_2
- Outcome: outcome (WIN/LOSS/BE/OPEN), close_price, pnl_usd, pnl_r
- Market: h1_rsi, h1_atr, h1_trend, m15_structure, session_name
- AI scores: qwen_confidence, claude_risk_score, rr_ratio, macro_bias, macro_modifier
- Timestamps: opened_at, closed_at, created_at, updated_at
- Indexes: opened_at, outcome, setup_type+session_name, instance_id+outcome, symbol+strategy_version+outcome

### trade_audit_log
- trade_id, field_name, old_value, new_value, changed_at, changed_by

### backtest_runs
- run_id (unique), symbol, name, state (pending/running/completed/failed/paused/killed)
- days, **timeframe** (String 8, default "1h"), balance, max_generations, generation, phase, message
- best_sharpe, best_wr, best_pnl, best_dd, best_trades, generations_json (JSON)
- pid, heartbeat_at, checkpoint_path, error_message, error_traceback

### app_settings
- key (PK, String 128), value (Text nullable), updated_at (DateTime)

### research_reports
- symbol, strategy_version, report_date, period_start/end
- win_rate, avg_rr, total_pnl_usd, sharpe_ratio
- setup_stats (JSON), session_stats (JSON), analysis_summary, improvement_suggestions (JSON)

### parameter_snapshots
- snapped_at, trigger, parameters (JSON), sharpe_at_snapshot, win_rate_at_snapshot, notes

### evolution_events
- occurred_at, symbol, strategy_version, event_type
- metrics_before (JSON), metrics_after (JSON), params_before (JSON), params_after (JSON), details

### running_instances
- symbol, instance_id (unique), pid, started_at, strategy_version

### pipeline_decisions
- occurred_at, symbol, direction, allowed (bool), blocked_by, block_reason
- size_modifier (float), bias, tool_results (JSON), instance_id

### rejection_log
- occurred_at, symbol, direction, setup_type, session_name, rejected_by, reason, instance_id

## Trading Loop Flow (trading/loop.py)
```
1. Check RiskMonitor.can_open_trade()
   └── daily loss cap, consecutive loss limit, max concurrent, portfolio heat
2. Build MarketContext
   └── fetch OHLCV (H1, M15, M5, D1), compute indicators, fetch news, evaluate session, get DXY
3. Run FilterPipeline (10 tools in sequence)
   └── session → news → volatility → dxy → sentiment → risk → bos → fvg → vwap → correlation
   └── short-circuit on first "block" result
   └── accumulate size_modifier (product of all tools)
4. Generate signal via AI (MultiAssetSignalEngine)
   └── build system + user prompts from AssetConfig + MarketContext
   └── call AICaller.call_model() → parse JSON → TradeSignal
5. Validate signal (UniversalValidator)
   └── Stage 1: HardRuleChecker (9 deterministic checks, zero API cost)
   └── Stage 2: AI validation (Claude risk assessment, optional)
   └── Output: ValidatedSignal with approval status + risk_score
6. Compute position size (PositionSizer)
   └── risk$ = balance × risk_pct × macro_modifier × dd_modifier
   └── lots = risk$ / (SL_distance × pip_value) with margin cap
7. Execute order (MT5Executor)
   └── live: MT5 market order with magic number
   └── dry-run: synthetic execution with logged intent
8. Log to DB, emit events (TradeOpened), notify (Telegram)
```

## AI Provider Architecture (ai/)
```
AICaller.call_model(model_id, messages)
  ├── model_hub.get_model_by_id(model_id) → ModelConfig
  ├── rate_limiter.acquire(provider)
  ├── _resolve_key(provider) → API key from dict, env var, or ""
  └── _dispatch(cfg, key, messages) → routes to:
      ├── AnthropicProvider.call()      — httpx async POST to api.anthropic.com
      ├── GeminiProvider.call()         — google.generativeai SDK
      ├── OpenAICompatProvider.call()   — httpx async POST to api.openai.com (or deepseek/xai/together.ai)
      └── OllamaProvider.call()         — httpx async POST to localhost:11434

Rate limiting: 10 calls/min/provider (sliding window)
Roles: signal (gemini-2.5-flash), validator (claude-sonnet-4-6), research (claude-sonnet-4-6)
```

## Config Resolution (core/config.py)
```
Priority:
  1. Runtime overrides (ConfigChanged events)
  2. Database (app_settings table via SettingsService)
  3. Environment variables / .env file
  4. Hardcoded defaults in config.py

AppConfig(BaseSettings):
  ├── broker:    BrokerConfig    (server, login, password, symbol, terminal_path)
  ├── risk:      RiskConfig      (risk_pct, max_daily_loss, leverage, hard cap validators)
  ├── signal:    SignalConfig     (40+ params: confidence, RR, volatility, guards, circuit breaker)
  ├── session:   SessionConfig   (london/ny hours, news windows, spread)
  ├── api:       APIConfig       (6 provider keys, model selections, qwen config)
  ├── evolution: EvolutionConfig (drift limits, rollback thresholds, promotion gates)
  ├── db:        DBConfig        (url, pool_size)
  ├── telegram:  TelegramConfig  (token, chat_id, enabled)
  ├── dry_run:   bool
  ├── log_level: str
  └── environment: str
```

## Event Bus (core/events.py)
```
EventBus.publish(event) → concurrent handler execution via asyncio.gather
  Handlers subscribe by event type class
  Error isolation: one handler crash doesn't affect others

Events (13 types):
  SignalGenerated(symbol, signal)
  SignalValidated(symbol, signal, approved)
  SignalRejected(symbol, reason, rejected_by)
  TradeOpened(symbol, direction, entry_price, lot_size, trade_id)
  TradeClosed(symbol, outcome, pnl_usd, trade_id)
  PipelineBlocked(symbol, blocked_by, reason)
  RiskLimitHit(limit_type, details)
  ResearchCompleted(symbol, report_id)
  ConfigChanged(keys, source)
  StrategyPromoted(symbol, version, from_status, to_status)
  SeedLabProgress(run_id, phase, current, total, message)
  CanaryStarted(symbol, canary_id, allocation_pct, duration_hours)
  CanaryEnded(symbol, canary_id, recommendation)
```

## Risk Management (risk/)

### PositionSizer
```
Input: validated_signal, macro_modifier, atr_h1, rolling_dd_modifier
Algorithm:
  risk_usd = balance × risk_pct × macro_modifier × dd_modifier
  sl_distance = abs(entry - sl)
  slippage_buffer = 1.15 (metals) / 1.30 (crypto)
  lots = risk_usd / (sl_distance × pip_value × slippage_buffer)
  margin_check: lots × margin_per_lot ≤ balance × margin_cap_pct
Output: {lots, risk_usd, risk_pct, sl_distance, margin_required, margin_pct}
```

### RiskMonitor
```
Tracked limits:
  - Daily loss cap (3% default)
  - Session loss cap (1%)
  - Consecutive loss limit (5)
  - Max concurrent trades (2)
  - Portfolio heat (3% max open risk)
  - Max trades per hour (3)
Methods: seed_from_db(), can_open_trade(), record_trade_close()
Kill switch: force_close_all when daily limit hit
```

### Guards
- SignalHashFilter: reject duplicate setups (rolling window)
- ConfidenceVarianceFilter: reject unstable AI confidence
- SpreadRegimeFilter: reject spread spikes
- EquityCurveScaler: scale position size based on equity curve

### Repositioner
- TradeRepositioner: triggers on opposite signal, news risk, volume/volatility spikes
- Outputs RepositionEvent list with actions (partial close, trail SL, etc.)

## Complete Tools & Guards Inventory

### Category 1: Pipeline Filters (toggleable, run every cycle)
```
FilterPipeline.run(context) → {allow_trade, block_reason, blocked_by, size_modifier, bias}
  Sequential execution, short-circuit on block:
    1. session_filter    — tradeable session (overlap=1.0 > london=0.80 > ny=0.85 > asia=0.20; blocks weekend)
                           Params: MIN_SESSION_SCORE (default: 0.55)
    2. news_filter       — high-impact news blackout
                           Params: NEWS_PRE_MINUTES (30), NEWS_POST_MINUTES (15)
    3. volatility_filter — ATR range check (block extreme + dead markets)
                           Params: MAX_VOLATILITY_ATR_PCT (2.5), MIN_VOLATILITY_ATR_PCT (0.05)
    4. dxy_filter        — USD correlation for gold/forex (blocks conflicting direction; 60min cache)
    5. sentiment_filter  — Polymarket macro (never blocks; reduces size 50% on conflict; 60min cache)
    6. risk_filter       — delegates to RiskMonitor (daily loss, kill switch, max concurrent)

Each tool returns: ToolResult(passed, reason, severity, size_modifier, bias, latency_ms)
Pipeline accumulates: size_modifier = product of all tool modifiers (floor 0.20)
```

### Category 2: Validation Rule Guards (toggleable per-strategy)
```
Run as hard rule checks before AI validation (zero API cost):
  - EMA200 Trend Filter    — block trades against EMA200 direction (USE_EMA200_FILTER)
  - BOS Structure Guard    — M15 close must break swing H/L (USE_BOS_GUARD, BOS_MIN_BREAK_ATR=0.2, BOS_SWING_LOOKBACK=20)
  - FVG Structure Guard    — entry must be inside Fair Value Gap (CHECK_FVG, FVG_MIN_SIZE_ATR=0.15, FVG_LOOKBACK=20)
  - Tick Jump Guard        — reject 2-bar spikes > threshold (CHECK_TICK_JUMP, TICK_JUMP_ATR_MAX=0.8)
  - Liquidity Vacuum Guard — reject thin-body spike candles (CHECK_LIQ_VACUUM, LIQ_VACUUM_SPIKE_MULT=2.5, LIQ_VACUUM_BODY_PCT=30)
  - VWAP Guard             — block entries > N ATR from VWAP (USE_VWAP_GUARD, VWAP_EXTENSION_MAX_ATR=1.5)
```

### Category 3: Stateful Guards (always-on system protection)
```
Instantiated once, state persists across cycles. Located in risk/guards.py:
  - SignalHashFilter         — dedup identical setups within window (GUARD_SIGNAL_HASH_WINDOW=3)
  - ConfidenceVarianceFilter — reject unstable AI confidence (GUARD_CONF_VARIANCE_WINDOW=3, MAX_STDEV=0.15)
  - SpreadRegimeFilter       — reject spread spikes vs median (GUARD_SPREAD_REGIME_WINDOW=50, THRESHOLD=1.8)
  - EquityCurveScaler        — halve risk when equity < MA (GUARD_EQUITY_CURVE_WINDOW=20, SCALE=0.5)
  - DrawdownPauseGuard       — pause 30min on 3 accelerating losses (GUARD_DD_PAUSE_MINUTES=30, LOOKBACK=3)

Additional hardcoded checks in trading loop:
  - Portfolio Risk Cap       — block when total open risk ≥ risk_pct × max_concurrent (GUARD_PORTFOLIO_CAP_ENABLED)
  - Correlation Guard        — block/reduce correlated positions (USE_CORRELATION_GUARD, BLOCK=0.90, REDUCE=0.75)
  - Near-Position Dedup      — skip if open trade within N ATR (GUARD_NEAR_DEDUP_ATR=1.0)
```

### Category 4: Position Management (live trades)
```
TradeRepositioner — runs every cycle for each open trade. 4 independent triggers:
  - opposite_signal    — full close if new signal conflicts (REPOSITIONER_OPPOSITE_SIGNAL)
  - news_risk          — SL→BE or 50% close before news (REPOSITIONER_NEWS_RISK, WINDOW=15min)
  - volume_spike       — SL→BE if M15 vol ≥ 2.5× avg AND in profit (REPOSITIONER_VOLUME_SPIKE, MULT=2.5)
  - volatility_spike   — SL→BE if H1 ATR ≥ 1.8× baseline AND in profit (REPOSITIONER_VOLATILITY_SPIKE, MULT=1.8)

Master toggle: REPOSITIONER_ENABLED
All triggers toggleable independently via settings UI.
```

### Settings Keys Summary (all configurable via WebUI → Tools tab)
```
Pipeline:      tool_enabled_{session,news,volatility,dxy,sentiment,risk}_filter + params
Validation:    USE_EMA200_FILTER, USE_BOS_GUARD, CHECK_FVG, CHECK_TICK_JUMP, CHECK_LIQ_VACUUM, USE_VWAP_GUARD + params
Stateful:      GUARD_{SIGNAL_HASH,CONF_VARIANCE,SPREAD_REGIME,EQUITY_CURVE,DD_PAUSE}_* + PORTFOLIO_CAP + CORRELATION + NEAR_DEDUP
Repositioner:  REPOSITIONER_{ENABLED,OPPOSITE_SIGNAL,NEWS_RISK,VOLUME_SPIKE,VOLATILITY_SPIKE} + params
Mode overrides: tool_enabled_risk_filter_{dry_run,backtest,live}
```

### Backtest Tool Compatibility
```
Tools usable in backtests (operate on historical OHLCV, no external API):
  ✓ session_filter      — checks bar timestamp vs London/NY hours
  ✓ volatility_filter   — checks ATR% range from bar data
  ✓ ema200_filter       — checks price vs EMA200 direction
  ✓ bos_guard           — checks swing high/low breaks from bars
  ✓ fvg_guard           — checks fair value gap from 3-bar patterns
  ✓ tick_jump_guard     — checks 2-bar move magnitude
  ✓ liq_vacuum_guard    — checks candle body % vs range
  ✓ vwap_guard          — checks price extension from EMA21 proxy

NOT usable in backtests (need live data, external APIs, or stateful context):
  ✗ news_filter         — needs live economic calendar
  ✗ dxy_filter          — needs live DXY feed
  ✗ sentiment_filter    — needs Polymarket API
  ✗ risk_filter         — needs live RiskMonitor state
  ✗ correlation_guard   — needs open positions across symbols
  ✗ portfolio_risk_cap  — needs live portfolio
  ✗ signal_hash_dedup   — stateful across cycles
  ✗ confidence_variance — stateful across cycles
  ✗ spread_regime       — needs live spread data
  ✗ equity_curve_scaler — stateful across trades
  ✗ drawdown_pause      — stateful across trades
  ✗ near_dedup          — needs open trade context
  ✗ repositioner        — needs live position management

Backtest API (POST /api/backtests) accepts per-tool toggles:
  use_session_filter, use_volatility_filter, use_ema200_filter,
  use_bos_guard, use_fvg_guard, use_tick_jump_guard,
  use_liq_vacuum_guard, use_vwap_guard

Timeframe support: 1m, 5m, 15m, 30m, 1h (default), 1d, 1wk, 1mo
  yfinance limits: 1m→7d max, 5m/15m/30m→60d, 1h→730d, 1d+→unlimited
  **Thread-pool execution**: All `engine.run()` calls execute via `_run_engine_in_thread()` — creates a fresh event loop in a thread pool worker via `asyncio.to_thread(lambda: asyncio.run(engine.run(...)))`. This prevents CPU-bound bar iteration (35K+ bars on M15/365d) from blocking the uvicorn event loop. HTTP/WebSocket remain responsive during backtests.
```

## Multi-Asset Support (config/assets.py)
```
AssetConfig per symbol:
  - Identity: symbol, display_name, asset_class (spot_metal/crypto/forex)
  - Sessions: best_sessions, min_session_score, avoid_sessions
  - Position: pip_value_per_lot, pip_size, min/max lot, lot_step
  - SL/TP: atr_mult, rr ratios, min/max points
  - Indicators: EMA periods, RSI levels, ATR period
  - Spread/volatility limits
  - Correlation symbol

Pre-configured: XAUUSD, BTCUSD, ETHUSD, EURUSD, GBPUSD, USDJPY, and more
```

## Patterns & Lessons Learned

### Background Task Execution
**Problem:** `POST /api/backtests` only created a DB record with state="pending" — nothing actually picked it up and ran the engine. The `BacktestEngine` (402 lines, fully implemented) was dead code.

**Solution:** `backtester/runner.py` bridges the gap:
1. `POST` handler calls `runner.start_backtest()` which spawns an `asyncio.Task`
2. Task fetches OHLCV data (yfinance), runs `BacktestEngine.run()`, updates DB progress
3. Module-level dicts track state: `_tasks`, `_stop_flags`, `_logs`

**Rule:** Any "queue and run" endpoint MUST have a runner that actually executes the queued work. A DB record with state="pending" is not execution.

### Session Factory for Background Tasks
**Problem:** Background `asyncio.Task`s need a DB session factory but run outside FastAPI request context (no `Request` object available).

**Solution:** `deps._app_ref` is set by `app.py` at startup. Background tasks call `deps._get_session_factory()` to get the session factory from app state. Each DB operation in the background task creates its own session scope:
```python
async with session_factory() as session:
    repo = BacktestRepository(session)
    await repo.update_state(run_id, "running")
    await session.commit()
```

**Rule:** Background tasks must create their own short-lived sessions per operation, not hold a single session for the entire task lifetime.

### Graceful Stop via Flags
**Pattern:** Use a `_stop_flags: dict[str, bool]` instead of `task.cancel()`:
- `request_stop(run_id)` sets flag to `True`
- Engine loop checks `if _stop_flags.get(run_id): break`
- Task sets state to "paused" and exits cleanly
- `task.cancel()` is only used in `delete_run_data()` as last resort

**Why:** `cancel()` raises `CancelledError` which can corrupt in-progress DB writes. Flag-based stop allows the task to finish its current iteration and save state.

### Stale "running" State After Server Restart
**Problem:** Background tasks (`_tasks` dict) are in-memory only. If the server restarts while a backtest is running, the DB state stays "running" but the asyncio task is gone. The UI shows Stop button but Stop returns "Not running", and Delete button is hidden.

**Solution (two parts):**
1. **Backend:** PATCH `/stop` detects stale state — if `request_stop()` returns False AND DB state is "running", auto-fix state to "paused" with message "task lost on server restart"
2. **Frontend:** Button logic uses `is_running` (live task check) not `state` (DB, can be stale). `canDelete = !is_running` always allows delete when task is dead.

**Rule:** In-memory task state (`_tasks`, `_stop_flags`, `_logs`) is ephemeral. DB state can become orphaned. All endpoints that read state must handle the mismatch between DB "running" and task-not-found.

### Log Buffering
**Pattern:** In-memory ring buffer per run_id, not file-based:
- `_logs: dict[str, list[str]]` with max 500 lines
- `_log(run_id, msg)` appends timestamped line
- `GET /api/{id}/logs?offset=N` returns lines from offset (client tracks position)
- Logs cleared on delete

**Why:** File-based logs add I/O overhead and cleanup complexity. In-memory is fine for monitoring — logs are ephemeral (lost on server restart, which is acceptable for backtest output).

### Backtest Data Source: MT5 Primary, yfinance Fallback
The backtest runner uses a **dual data source** strategy:
- **MT5 primary**: No day/timeframe limits. Tried first via `MetaTrader5.copy_rates_from_pos()`. **Auto-connects** using broker credentials from `AppConfig.broker` (server, login, password, terminal_path). Falls back to bare `mt5.initialize()` if no credentials configured. Handles broker symbol variants (e.g. `XAUUSD`, `XAUUSDm`).
- **yfinance fallback**: Used when MT5 is unavailable (e.g. `--web-only` mode, MT5 not installed, symbol not found in MT5).
- **Synthetic fallback**: Random-walk data as last resort if both fail.
- Server can run in `--web-only` mode (no MT5 connection) — yfinance handles it
- **116 yfinance-compatible symbols** across 13 asset classes — served via `data/yf_catalog.py`
- Asset classes: Metals (5), Crypto (17), Forex Majors (7), Forex Crosses (15), Indices (11), Index Futures (4), Energy (5), Agriculture (7), US Mega-Cap Stocks (15), US Tech Stocks (13), Popular ETFs (13), Volatility (1), Bonds (3)
- Symbol mapping via `yf_catalog.SYMBOL_TO_YF`: `XAUUSD → GC=F`, `BTCUSD → BTC-USD`, `NVDA → NVDA`, etc.
- `GET /api/backtests/symbols` serves the full catalog for frontend searchable dropdown (grouped by asset class)
- Frontend: searchable dropdown replaces hardcoded text input — user can filter by symbol, name, yfinance ticker, or group name; also accepts custom symbol entry via Enter key
- **MT5 has no timeframe limits** — any symbol, any days, any timeframe
- yfinance fallback limits: 1m (7d), 5m/15m/30m (60d), 1h (730d), 1d+ (unlimited) — backend auto-caps when falling back
- Frontend allows up to 730d on all timeframes; shows amber warning if yfinance fallback would cap
- Fallback chain: MT5 → yfinance → synthetic random-walk
- **Naming**: Auto-generated creative names (`{adj}-{noun}-{SYMBOL}_v1`). Always v1 — backtests are fresh experiments. Versioning (v2, v3...) belongs to strategy cards via auto-learn/mutation pipeline.
- Data fetched once per backtest run, split 80/20 for train/validation

### Optuna Optimization Pipeline
Generation loop flow (ported from v1's asset_trainer.py):
```
Gen 1: Baseline — run with default BacktestParams → establish baseline Sharpe
Gen 2+: Optuna TPE Optimization
  1. suggest_params() — mutate ±15% of current best (bounded, pruned)
  2. Run 30 trials on TRAIN split (80% of bars)
  3. Best trial → validate on HOLDOUT split (20% of bars)
  4. If train-val gap > 0.30 → OVERFIT DETECTED → reject
  5. If full-data Sharpe > best + 0.05 → ACCEPT new params
  6. If no improvement for 2 consecutive gens → EARLY STOP
```
Tunable parameters (7): ema_fast, ema_slow, sl_atr_mult, tp1_rr, tp2_rr, rsi_ob, rsi_os
Threading model: entire Optuna loop runs in thread pool via asyncio.to_thread().
Each trial uses asyncio.run() for a fresh event loop — never touches the main uvicorn loop.
This keeps the server responsive (HTTP + WebSocket) while backtests run.
CRITICAL: do NOT use run_coroutine_threadsafe() for blocking workloads — it starves the event loop.

## Total Codebase
- ~145 Python files
- ~13,500 lines of code
- ~95% real implementation
