# AlphaLoop v3 — Architecture Quick Reference

## Purpose
Quick lookup for key files, entry points, settings, event types, and route modules.

---

## Entry Points

| Entry | File | Lines | Description |
|-------|------|-------|-------------|
| CLI | `src/alphaloop/main.py` | ~145 | argparse, signal handlers, main_async() |
| App factory | `src/alphaloop/app.py` | ~40 | create_app() → Container |
| WebUI factory | `src/alphaloop/webui/app.py` | ~148 | FastAPI factory, router mounts, lifespan |

---

## Core Framework

| File | Lines | Responsibility |
|------|-------|---------------|
| `core/config.py` | 197 | AppConfig (Pydantic BaseSettings), 8 sub-configs |
| `core/container.py` | 45 | DI container: config, event_bus, db_engine, session_factory |
| `core/events.py` | 194 | EventBus + 15 event dataclasses |
| `core/lifecycle.py` | 35 | startup/shutdown hooks, seed 83+ settings |
| `core/constants.py` | 72 | RISK_HARD_CAPS, POLL_INTERVAL, circuit breaker thresholds |
| `core/types.py` | 86 | 11 StrEnum types |
| `core/errors.py` | 38 | 8 exception classes |

---

## Event Types (15)

| Event | Key Fields |
|-------|-----------|
| `SignalGenerated` | symbol, signal |
| `SignalValidated` | symbol, signal, approved |
| `SignalRejected` | symbol, reason, rejected_by |
| `TradeOpened` | symbol, direction, entry_price, lot_size, trade_id |
| `TradeClosed` | symbol, outcome, pnl_usd, trade_id |
| `PipelineBlocked` | symbol, blocked_by, reason |
| `RiskLimitHit` | symbol, limit_type, details |
| `ResearchCompleted` | symbol, report_id |
| `ConfigChanged` | keys, source |
| `StrategyPromoted` | symbol, version, from_status, to_status |
| `SeedLabProgress` | run_id, phase, current, total, message |
| `CanaryStarted` | symbol, canary_id, allocation_pct, duration_hours |
| `CanaryEnded` | symbol, canary_id, recommendation |
| `MetaLoopCompleted` | symbol, action_taken, new_version, details |
| `StrategyRolledBack` | symbol, from_version, to_version, reason |

---

## WebUI Route Modules (14)

| File | Prefix | Endpoints |
|------|--------|-----------|
| `routes/dashboard.py` | `/api/dashboard` | 1 GET |
| `routes/trades.py` | `/api/trades` | 3 GET |
| `routes/bots.py` | `/api/bots` | GET, POST, DELETE, POST start, POST stop |
| `routes/backtests.py` | `/api/backtests` | GET symbols, GET, GET/{id}, POST, PATCH stop/resume, DELETE, GET logs |
| `routes/tools.py` | `/api/tools` | 2 GET |
| `routes/ai_hub.py` | `/api/ai-hub` | GET, PUT |
| `routes/research.py` | `/api/research` | 2 GET |
| `routes/settings.py` | `/api/settings` | GET, PUT |
| `routes/seedlab.py` | `/api/seedlab` | GET, POST, GET logs, PATCH stop, DELETE |
| `routes/strategies.py` | `/api/strategies` | GET, evaluate, promote, activate, delete, canary start/end, models PUT |
| `routes/test_connections.py` | `/api/test` | POST mt5/telegram/ai/ai-key/ollama, GET models |
| `routes/websocket.py` | `/ws` | WebSocket event stream |
| `routes/live.py` | `/api/live` | Live trading data, symbols, sessions |
| `routes/risk_dashboard.py` | `/api/risk` | Risk metrics |
| `routes/event_log.py` | `/api/events` | Event history |

---

## Database Models (7)

| Model | File | Key Columns |
|-------|------|------------|
| `AppSetting` | `db/models/settings.py` | key, value |
| `TradeLog` | `db/models/trade.py` | symbol, direction, entry_price, lot_size, outcome, pnl_usd (100+ cols, 6 indexes) |
| `TradeAuditLog` | `db/models/trade.py` | trade_id, action, details |
| `BacktestRun` | `db/models/backtest.py` | run_id, symbol, state, generation, best_sharpe |
| `ResearchReport` | `db/models/research.py` | symbol, strategy_version, metrics |
| `ParameterSnapshot` | `db/models/research.py` | version, params_json |
| `EvolutionEvent` | `db/models/research.py` | event_type, symbol, details |
| `PipelineDecision` | `db/models/pipeline.py` | symbol, decision, blocked_by |
| `RejectionLog` | `db/models/pipeline.py` | symbol, reason, rejected_by |
| `RunningInstance` | `db/models/instance.py` | symbol, instance_id, pid |
| `StrategyVersion` | `db/models/strategy.py` | symbol, version, status, params |

---

## Tool Plugins (10)

| Plugin | Type | Purpose |
|--------|------|---------|
| `session_filter` | Filter | Market session score check |
| `news_filter` | Filter | High-impact news blackout |
| `volatility_filter` | Filter | ATR% range check |
| `dxy_filter` | Filter | DXY correlation check |
| `sentiment_filter` | Filter | Polymarket sentiment |
| `risk_filter` | Filter | Per-mode risk rules |
| `bos_guard` | Guard | Break of structure validation |
| `fvg_guard` | Guard | Fair value gap entry check |
| `vwap_guard` | Guard | VWAP extension limit |
| `correlation_guard` | Guard | Cross-asset correlation |

---

## Config Sub-Models

| Model | Key Fields |
|-------|-----------|
| `BrokerConfig` | server, login, password, symbol, terminal_path, magic |
| `RiskConfig` | risk_per_trade_pct (1%), max_daily_loss_pct (3%), max_concurrent_trades (2), leverage (100) |
| `SignalConfig` | min_confidence (0.70), min_rr_ratio (1.5), 25+ params |
| `SessionConfig` | london_open/close, ny_open/close, spread limits |
| `APIConfig` | 6 provider API keys, model selections |
| `EvolutionConfig` | min_trades_for_tuning (30), drift limits, rollback thresholds |
| `DBConfig` | url, pool_size, echo |
| `TelegramConfig` | token, chat_id, enabled |
