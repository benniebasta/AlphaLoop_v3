# AlphaLoop v3 -- Implementation Plan

## Context

AlphaLoop v2 (in `C:\Users\benz-\Documents\tradingai\alphaloop`) is a working multi-asset AI trading system with signal generation, validation, backtesting, strategy discovery, and a web dashboard. It has grown organically, resulting in monolithic files (`server.py` at ~5,800 lines, `main.py` at ~2,900 lines, `index.html` at ~11,400 lines), circular import risks from global singletons, mixed sync/async, duplicate utility files, and SQLite-only storage.

**alphaloop_v3** (`C:\Users\benz-\Documents\alphaloop_v3`, currently empty) is a ground-up rewrite preserving all v2 functionality while fixing these architectural issues.

---

## Project Structure

```
alphaloop_v3/
├── pyproject.toml                     # PEP 621 deps + scripts
├── alembic.ini                        # DB migrations
├── .env.example
├── README.md
│
├── src/alphaloop/
│   ├── __init__.py                    # __version__
│   ├── app.py                         # Application factory (wires DI container)
│   ├── main.py                        # CLI entry point (~100 lines)
│   │
│   ├── core/                          # Framework building blocks
│   │   ├── config.py                  # Pydantic BaseSettings (layered: defaults → .env → DB)
│   │   ├── container.py               # DI container (replaces global singletons)
│   │   ├── events.py                  # Async event bus (publish/subscribe)
│   │   ├── lifecycle.py               # Startup/shutdown hooks
│   │   ├── constants.py               # All magic numbers with docstrings
│   │   ├── types.py                   # Shared enums (TrendDirection, SetupType, etc.)
│   │   └── errors.py                  # Exception hierarchy
│   │
│   ├── db/                            # Database layer (async SQLAlchemy)
│   │   ├── engine.py                  # Async engine factory (SQLite / PostgreSQL)
│   │   ├── session.py                 # Async session context manager
│   │   ├── models/                    # One file per domain
│   │   │   ├── base.py               # DeclarativeBase, TimestampMixin
│   │   │   ├── settings.py           # AppSetting
│   │   │   ├── trade.py              # TradeLog, TradeAuditLog
│   │   │   ├── research.py           # ResearchReport, ParameterSnapshot, EvolutionEvent
│   │   │   ├── pipeline.py           # PipelineDecision, RejectionLog
│   │   │   ├── backtest.py           # BacktestRun
│   │   │   └── instance.py           # RunningInstance
│   │   ├── repositories/             # Typed async data access
│   │   │   ├── settings_repo.py
│   │   │   ├── trade_repo.py
│   │   │   ├── research_repo.py
│   │   │   └── backtest_repo.py
│   │   └── migrations/               # Alembic
│   │       ├── env.py
│   │       └── versions/
│   │
│   ├── config/                        # Domain configuration
│   │   ├── assets.py                  # AssetConfig (Pydantic), ASSET_CATALOG
│   │   ├── strategy_params.py         # StrategyParams (Pydantic)
│   │   └── settings_service.py        # Merges env + DB overrides (async)
│   │
│   ├── ai/                            # AI model hub + callers
│   │   ├── model_hub.py               # ModelConfig, catalog, role resolution
│   │   ├── caller.py                  # Async universal caller (routes to providers)
│   │   ├── rate_limiter.py            # Per-provider async sliding window
│   │   └── providers/
│   │       ├── anthropic.py
│   │       ├── gemini.py
│   │       ├── openai_compat.py       # OpenAI, DeepSeek, xAI, Qwen
│   │       └── ollama.py
│   │
│   ├── signals/                       # Signal generation
│   │   ├── schema.py                  # TradeSignal, ValidatedSignal (Pydantic v2)
│   │   └── engine.py                  # MultiAssetSignalEngine (async)
│   │
│   ├── validation/                    # Signal validation
│   │   ├── validator.py               # UniversalValidator (async, two-stage)
│   │   ├── rules.py                   # HardRuleChecker (pure sync functions)
│   │   └── prompts.py                 # Validator prompt templates
│   │
│   ├── tools/                         # Filter/guard plugin system
│   │   ├── base.py                    # ToolResult (Pydantic), BaseTool ABC (async)
│   │   ├── registry.py               # Auto-discovery from plugins/
│   │   ├── pipeline.py               # FilterPipeline (async, short-circuit)
│   │   └── plugins/                   # Self-contained tool packages
│   │       ├── session_filter/
│   │       ├── news_filter/
│   │       ├── volatility_filter/
│   │       ├── dxy_filter/
│   │       ├── sentiment_filter/
│   │       ├── risk_filter/
│   │       ├── bos_guard/
│   │       ├── fvg_guard/
│   │       ├── vwap_guard/
│   │       └── correlation_guard/
│   │
│   ├── risk/                          # Risk management
│   │   ├── sizer.py                   # PositionSizer
│   │   ├── monitor.py                 # RiskMonitor (kill switch, daily limits)
│   │   ├── guards.py                  # Drawdown guards, hash filter, spread filter
│   │   └── repositioner.py            # TP1 partial, SL management, trailing
│   │
│   ├── data/                          # Market data layer
│   │   ├── fetcher.py                 # OHLCVFetcher (async, MT5 via to_thread)
│   │   ├── indicators.py             # Pure functions: RSI, EMA, ATR, VWAP, BOS, FVG, MACD, Bollinger, ADX, volume_ma, swing_highs_lows
│   │   ├── market_context.py          # MarketContext Pydantic model + async builder
│   │   ├── news.py                    # Async news calendar
│   │   ├── dxy.py                     # Async DXY fetch
│   │   └── polymarket.py              # Async sentiment fetch
│   │
│   ├── execution/                     # Broker execution
│   │   ├── mt5_executor.py            # MT5 (sync wrapped in to_thread)
│   │   └── schemas.py                 # OrderResult, Position (Pydantic)
│   │
│   ├── trading/                       # Main trading loop (extracted from v2 main.py)
│   │   ├── loop.py                    # Core trading cycle (~300 lines)
│   │   ├── circuit_breaker.py         # API failure tracking
│   │   └── heartbeat.py              # Health heartbeat writer
│   │
│   ├── research/                      # Auto-research loop
│   │   ├── analyzer.py                # Async performance analysis
│   │   ├── applier.py                 # Parameter application with guardrails
│   │   ├── evolution_guard.py         # Drift detection, OOS validation, rollback
│   │   ├── monte_carlo.py             # Monte Carlo simulation
│   │   └── prompts.py                # Research AI prompt templates
│   │
│   ├── seedlab/                       # Strategy discovery pipeline
│   │   ├── runner.py                  # Pipeline orchestrator
│   │   ├── background_runner.py       # Background task execution (asyncio.Task)
│   │   ├── seed_generator.py          # Template + combinatorial seeds
│   │   ├── regime_detector.py         # Market regime classification
│   │   ├── regime_runner.py           # Multi-regime backtest
│   │   ├── metrics.py                # SeedMetrics
│   │   ├── stability.py              # Cross-regime stability analysis
│   │   ├── ranking.py                # Composite scoring
│   │   ├── strategy_card.py          # Immutable output artifact
│   │   ├── registry.py               # Card persistence
│   │   ├── filter_node.py            # Filter metadata
│   │   └── cli.py                    # Subprocess entry point
│   │
│   ├── backtester/                    # Backtesting engine
│   │   ├── engine.py                  # MT5BacktestEngine + BacktestResult
│   │   ├── runner.py                  # Background task runner with Optuna loop
│   │   ├── optimizer.py               # Optuna TPE optimizer
│   │   ├── params.py                  # BacktestParams Pydantic model
│   │   ├── auto_improve.py            # Bayesian optimization
│   │   ├── deployment_pipeline.py     # Stage transitions (candidate→dry_run→demo→live)
│   │   ├── parallel_backtest.py       # Multi-process backtest scheduling
│   │   └── asset_trainer.py           # SeedLab→Optuna→strategy version bridge
│   │
│   ├── webui/                         # Web dashboard
│   │   ├── app.py                     # FastAPI factory, mounts routers
│   │   ├── deps.py                    # DI: get_db, get_config, auth
│   │   ├── auth.py                    # Bearer token middleware
│   │   ├── routes/
│   │   │   ├── dashboard.py
│   │   │   ├── trades.py
│   │   │   ├── settings.py
│   │   │   ├── bots.py
│   │   │   ├── backtests.py
│   │   │   ├── tools.py
│   │   │   ├── ai_hub.py
│   │   │   ├── research.py
│   │   │   ├── seedlab.py
│   │   │   └── websocket.py
│   │   └── static/
│   │       ├── index.html             # Shell HTML
│   │       ├── css/app.css
│   │       └── js/
│   │           ├── app.js             # SPA router
│   │           ├── api.js             # Fetch wrapper
│   │           └── components/        # One JS file per tab
│   │
│   ├── notifications/                 # Alerts (extracted from utils)
│   │   ├── telegram.py                # Async Telegram sender
│   │   └── dispatcher.py             # Batching + dedup
│   │
│   ├── monitoring/                    # Observability
│   │   ├── metrics.py                 # Ring buffer metrics
│   │   ├── logging.py                # structlog configuration
│   │   └── health.py                 # Health check aggregator
│   │
│   └── utils/                         # Genuine utilities only
│       ├── time.py                    # Session info, market hours
│       ├── crypto.py                  # Encryption helpers
│       └── credits.py                # AI credit tracking
│
├── tests/
│   ├── conftest.py                    # Shared fixtures
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── scripts/
│   ├── migrate_v2_db.py               # v2 SQLite → v3 migration
│   └── migrate_v2_settings.py         # v2 app_settings → v3 (68 keys, all tabs)
│
└── strategy_versions/                 # Git-tracked strategy JSONs
```

---

## Phase Breakdown

### Phase 0: Project Scaffolding (Foundation)
**Goal**: Bootable project with config, DB, and health endpoint.

**Files to create**:
- `pyproject.toml` — all deps (fastapi, uvicorn, sqlalchemy[asyncio], aiosqlite, pydantic-settings, structlog, httpx, optuna, pandas, numpy, MetaTrader5)
- `src/alphaloop/core/config.py` — Pydantic BaseSettings replacing v2's `config/settings.py`
- `src/alphaloop/core/constants.py` — all magic numbers from v2
- `src/alphaloop/core/types.py` — shared enums
- `src/alphaloop/core/errors.py` — exception hierarchy
- `src/alphaloop/core/events.py` — async EventBus
- `src/alphaloop/core/container.py` — DI container
- `src/alphaloop/db/engine.py` — async engine factory
- `src/alphaloop/db/session.py` — async session context manager
- `src/alphaloop/db/models/base.py` — DeclarativeBase + TimestampMixin
- `src/alphaloop/db/models/settings.py` — AppSetting
- `src/alphaloop/monitoring/logging.py` — structlog setup
- `src/alphaloop/webui/app.py` — minimal FastAPI with `/health`
- `tests/conftest.py` — in-memory SQLite fixture

**Port from v2**: `config/settings.py` → `core/config.py`, `storage/models.py` (engine parts) → `db/engine.py`

**Verify**: Server starts, `/health` returns OK, `pytest` passes.

---

### Phase 1: Domain Models & Schemas
**Goal**: All Pydantic schemas and DB models ported with repositories.

**Files to create**:
- `db/models/trade.py` — TradeLog, TradeAuditLog
- `db/models/research.py` — ResearchReport, ParameterSnapshot, EvolutionEvent
- `db/models/pipeline.py` — PipelineDecision, RejectionLog
- `db/models/backtest.py` — BacktestRun
- `db/models/instance.py` — RunningInstance
- `db/repositories/*.py` — async CRUD for each domain
- `signals/schema.py` — TradeSignal, ValidatedSignal (Pydantic v2)
- `execution/schemas.py` — OrderResult, Position
- `config/assets.py` — AssetConfig (Pydantic)
- `config/strategy_params.py` — StrategyParams (Pydantic)
- `config/settings_service.py` — env + DB merge
- Alembic initial migration

**Port from v2**: `storage/models.py` (split into 6 files), `signals/signal_schema.py`, `config/assets.py`, `config/strategy_params.py`

**Verify**: `alembic upgrade head` creates all tables. Schema unit tests pass.

---

### Phase 2: AI Layer (can parallel with Phase 3)
**Goal**: Async AI caller with all providers and model hub.

**Files to create**:
- `ai/model_hub.py` — ModelConfig (Pydantic), catalog, role resolution
- `ai/rate_limiter.py` — async per-provider sliding window
- `ai/providers/anthropic.py` — async Anthropic
- `ai/providers/gemini.py` — async Gemini
- `ai/providers/openai_compat.py` — async OpenAI/DeepSeek/xAI/Qwen
- `ai/providers/ollama.py` — async Ollama
- `ai/caller.py` — routes to correct provider

**Port from v2**: `ai/caller.py` (split into provider files), `ai/model_hub.py`

**Verify**: Mock-based unit tests for each provider. Rate limiter throttles correctly.

---

### Phase 3: Data Layer + Tools Plugin System (can parallel with Phase 2)
**Goal**: Market data, indicators, and filter pipeline with plugin architecture.

**Files to create**:
- `data/fetcher.py` — async OHLCV (MT5 via `to_thread`, yfinance fallback). Uses `yf_catalog.SYMBOL_TO_YF` for ticker mapping. Backtest runner auto-connects MT5 using `BrokerConfig` credentials (server, login, password, terminal_path) — no manual setup needed if Settings > Broker is configured.
- `data/yf_catalog.py` — **116 yfinance-compatible symbols** across 13 asset classes (metals, crypto, forex majors/crosses, indices, futures, energy, agriculture, stocks, ETFs, bonds). Provides `get_catalog_for_api()` for frontend dropdowns and `get_yf_ticker()` for broker→yfinance mapping.
- `data/indicators.py` — pure functions (EMA, RSI, ATR, VWAP, BOS, FVG, swing_highs_lows, MACD, Bollinger, ADX, volume_ma)
- `data/market_context.py` — MarketContext Pydantic model + async builder
- `data/news.py`, `data/dxy.py`, `data/polymarket.py` — async fetchers
- `tools/base.py` — ToolResult (Pydantic), BaseTool ABC (async `run()`)
- `tools/registry.py` — auto-discovery from `plugins/`
- `tools/pipeline.py` — async FilterPipeline with short-circuit
- All 10 tool plugins in `tools/plugins/*/`

**Port from v2**: `data/*`, `tools/base.py`, `tools/pipeline.py`, `tools/registry.py`, all tool directories → `tools/plugins/`

**Verify**: Pipeline runs with mock context and 3+ tools. Registry discovers plugins.

---

### Phase 4: Signal + Validation + Risk
**Goal**: Complete signal-to-execution path.

**Files to create**:
- `signals/engine.py` — async MultiAssetSignalEngine (accepts strategy_params)
- `signals/algorithmic.py` — AlgorithmicSignalEngine (deterministic EMA+RSI for Mode A/B)
- `validation/validator.py` — async UniversalValidator (accepts validation_overrides)
- `validation/rules.py` — HardRuleChecker
- `validation/prompts.py` — prompt templates
- `risk/sizer.py`, `risk/monitor.py`, `risk/guards.py`, `risk/repositioner.py`
- `execution/mt5_executor.py` — sync MT5 wrapped in `to_thread`
- `notifications/telegram.py` — async
- `notifications/dispatcher.py`
- `utils/time.py`, `utils/crypto.py`, `utils/credits.py`

**Port from v2**: `signals/multi_asset_engine.py`, `validation/*`, `risk/*`, `execution/mt5_executor.py`, `utils/*`

**Verify**: Integration test: mock signal → validate → size → mock execute. Events fire.

---

### Phase 5: Trading Loop
**Goal**: Main trading loop works end-to-end in dry-run mode.

**Files to create**:
- `trading/loop.py` — async trading cycle (~580 lines): strategy loading → pipeline → overlay → signal mode branching → validate → guards → size → execute → TradeClosed publish
- `trading/strategy_loader.py` — ActiveStrategyConfig, load_active_strategy(), build_strategy_pipeline()
- `trading/overlay_loader.py` — DryRunOverlayConfig, per-card dry-run overlay
- `trading/meta_loop.py` — MetaLoop (TradeClosed event-driven) + RollbackTracker
- `trading/health_monitor.py` — StrategyHealthMonitor: composite score (sharpe+winrate-drawdown-stagnation)
- `trading/micro_learner.py` — MicroLearner: per-trade param nudges (±1%/±5%)
- `trading/circuit_breaker.py` — API failure tracking
- `trading/heartbeat.py` — periodic health writer
- `app.py` — application factory
- `main.py` — CLI entry point (~100 lines)
- `core/lifecycle.py` — startup/shutdown hooks

**Port from v2**: `main.py` (2,893 lines → split into 5 small files)

**Verify**: `python -m alphaloop --symbol XAUUSD --dry-run` runs one mock cycle.

---

### Phase 6: WebUI (can parallel with Phase 7)
**Goal**: Full web dashboard with modular routes and split frontend.

**Files to create**:
- `webui/app.py` — FastAPI factory
- `webui/deps.py` — DI dependencies
- `webui/auth.py` — bearer token middleware
- 11 route files in `webui/routes/` (dashboard, trades, bots, backtests, tools, ai_hub, research, settings, seedlab, strategies, websocket)
- `webui/static/index.html` — shell HTML (9 sidebar tabs)
- `webui/static/css/app.css`
- `webui/static/js/app.js` — SPA router
- `webui/static/js/api.js` — fetch wrapper
- 9 JS component files in `webui/static/js/components/` (dashboard, trades, bots, backtests, tools, ai_hub, research, strategies, settings)

**Backtest Form**: Symbol selection uses a searchable dropdown populated from `GET /api/backtests/symbols` which serves the full **116-symbol yfinance catalog** (13 asset classes). No hardcoded symbols — user can search by name, ticker, or category, and also type a custom symbol.

**Port from v2**: `webui/server.py` (5,822 lines → 12 files), `webui/index.html` (11,449 lines → 15 files)

**Verify**: All API endpoints work with TestClient. Frontend loads and navigates tabs.

---

## Bug Log

### BUG-001: Backtest page infinite poll after server restart (FIXED 2026-03-29)
**Symptom**: Backtest page stuck, hammering `/api/backtests` + `/api/backtests/{id}/logs` in a tight loop every second.
**Root cause**: The backtest process was killed mid-run (crash / server restart) without updating DB state. Row stayed at `state="running"` forever. The UI polls while `state == "running"`, so it looped indefinitely.
**Fix**: `PATCH /api/backtests/{run_id}/stop` already handles this — when `request_stop()` returns False (task not in memory), it force-sets `state = "paused"`. Calling it once unblocked the UI.
**Prevention needed**: On server startup, `_run_backtest` should mark any stale `state="running"` rows as `"paused"` so restarts self-heal without manual intervention.

### BUG-002: Resume restarts backtest from scratch instead of checkpoint (FIXED 2026-03-29)
**Symptom**: Stopping a backtest mid-run and resuming it restarts from generation 1, discarding all progress.
**Root cause**: `PATCH /api/backtests/{run_id}/resume` called `start_backtest()` without passing `timeframe` or `tools`. Both defaulted (`timeframe="1h"`, `tools=[]`). The runner fetches OHLCV data with the (wrong) default timeframe → different bar count → `data_hash` mismatch → checkpoint discarded → full restart.
**Secondary cause**: `tools_json` was never written to the DB on backtest creation, so even a correct resume couldn't recover the original tool config.
**Fix**:
- `create_backtest` now saves `tools_json` to the DB row.
- `resume_backtest` now reads `run.timeframe` and `run.tools_json` from the DB and passes them to `start_backtest`.
- Duplicate `tools` computation in `create_backtest` removed.

---

### Phase 7: Research + SeedLab + Backtester (can parallel with Phase 6)
**Goal**: Port research loop, strategy discovery, and backtesting.

**Files to create**:
- `research/analyzer.py`, `research/applier.py`, `research/evolution_guard.py`, `research/monte_carlo.py`, `research/prompts.py`
- All 11 seedlab files (mostly direct port — seedlab is already well-structured)
- `backtester/engine.py`, `backtester/auto_improve.py`, `backtester/deployment_pipeline.py`, `backtester/parallel_backtest.py`, `backtester/asset_trainer.py`

**Port from v2**: `research/*`, `seedlab/*`, `backtester/*`, `training/optimizer.py` → merge into `backtester/engine.py`

**Verify**: Research report generates from test data. Backtest runs 1 generation. SeedLab evaluates 1 seed.

---

### Phase 8: Monitoring + Observability
**Goal**: Structured logging, metrics, health checks.

**Files to create**:
- `monitoring/metrics.py` — enhanced ring buffer
- `monitoring/logging.py` — structlog JSON output
- `monitoring/health.py` — component status aggregator

**Verify**: JSON logs appear. `/health` returns detailed status. Metrics timeseries works.

---

### Phase 9: Testing + Hardening
**Goal**: Comprehensive test coverage, v2 data migration.

**Files to create**:
- Expanded unit tests for all modules
- Integration tests for trading cycle, DB repos, WebUI routes, tool registry
- E2E test: boot → run 1 cycle → verify DB state
- `scripts/migrate_v2_db.py` — v2 SQLite → v3 migration
- `scripts/migrate_v2_settings.py` — **v2 settings migration** (68 keys: API keys, broker/MT5 credentials, AI models, risk params, signal thresholds, session windows, Telegram config, tool toggles, evolution guardrails, system config). Reads from v2 `app_settings` table, writes to v3. Supports `--dry-run` preview. Encrypted values (API keys, passwords, tokens) transferred as-is.

**Verify**: `pytest --cov` shows 80%+ on core paths. E2E passes. v2 migration runs.

---

## Phase Dependencies

```
Phase 0 (Scaffold)
    │
    v
Phase 1 (Models/Schemas)
    │
    ├──────────────┐
    v              v
Phase 2 (AI)    Phase 3 (Data+Tools)   ← parallel
    │              │
    └──────┬───────┘
           v
    Phase 4 (Signal+Validation+Risk)
           │
           v
    Phase 5 (Trading Loop)
           │
    ├──────┴──────┐
    v             v
Phase 6 (WebUI) Phase 7 (Research+SeedLab)  ← parallel
    │             │
    └──────┬──────┘
           v
    Phase 8 (Monitoring)
           v
    Phase 9 (Testing)
```

---

## Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **DI Container** over singletons | Eliminates circular imports, makes testing trivial |
| **Async EventBus** | Decouples trading loop from notifications, metrics, WebSocket |
| **Pydantic BaseSettings** | Type-safe config with layered resolution (defaults → .env → DB) |
| **Async SQLAlchemy** | Non-blocking DB, supports SQLite (dev) + PostgreSQL (prod) |
| **Repository pattern** | Typed async queries, no raw sessions in business logic |
| **Provider files** for AI | Each provider is isolated, testable, replaceable |
| **tools/plugins/** directory | Clean separation of framework vs plugin code |
| **Split main.py** 2,893 → 5 files | Single responsibility, testable trading loop |
| **Split server.py** 5,822 → 12 files | Route-per-domain, middleware as dependencies |
| **Split index.html** 11,449 → 15 files | ES modules, no build tooling needed |
| **Single utils/** package | Eliminates `training/utils/` duplication |
| **structlog** for logging | Structured JSON logs, context propagation |

---

## Critical v2 Files to Reference During Implementation

| v2 File | Why Critical |
|---------|-------------|
| `main.py` | Core trading cycle logic to decompose |
| `config/settings.py` | Full CONFIG structure (~180 fields) to recreate |
| `storage/models.py` | All 10 ORM models to split |
| `webui/server.py` | All API endpoints to preserve |
| `webui/index.html` | Full dashboard UI to split |
| `tools/registry.py` | Plugin discovery pattern to preserve |
| `signals/multi_asset_engine.py` | Provider catalog + prompt building |
| `validation/universal_validator.py` | Two-stage validation flow |
| `risk/sizer.py` | Position sizing math (must be exact) |
| `seedlab/runner.py` | Pipeline orchestration pattern |

---

## Verification Strategy

After each phase:
1. **Unit tests pass** — `pytest tests/unit/`
2. **Integration tests pass** — `pytest tests/integration/`
3. **Manual smoke test** — described in each phase's "Verify" section
4. **No circular imports** — `python -c "import alphaloop"` succeeds
5. **Type check** — `mypy src/` passes (optional but recommended)

Final verification:
- Full dry-run cycle with mock AI providers
- WebUI loads and all tabs functional
- v2 database migration runs without data loss
- All v2 API endpoints have v3 equivalents

---

## Strategy Lifecycle Flow

```
SeedLab                    Backtest/Optuna              Strategy Version           Deployment
[generate seeds]           [optimize params]            [create version JSON]      [promote via gates]
  |                          |                            |                          |
  +-- template seeds (10)    +-- Baseline (gen 1)         +-- strategy_versions/     +-- candidate
  +-- combinatorial          +-- Optuna TPE (gen 2+)      |   {SYMBOL}_v{N}.json    +-- dry_run (30+ trades, Sharpe>0.3)
  |                          +-- Walk-forward validate     +-- Params + metrics      +-- demo (50+ trades, Sharpe>0.5)
  +-- Regime detection       +-- Overfit detection         +-- Tool config           +-- live (100+ trades, Sharpe>0.7)
  +-- Multi-regime backtest  +-- Auto create version       +-- AI model config       |
  +-- Stability analysis     |                            |                          +-- activate via API
  +-- Strategy card build    +-- Checkpoint save/load     +-- DB registration        |
  +-- Registry save          +-- Resume from pause        |                          |
                                                          v                          v
                                                     Monitoring ──> Retraining ──> SeedLab
                                                     [research/analyzer.py]
                                                     [check_retraining_needed()]
                                                     [Sharpe degradation < 70%]
```

### API Endpoints for Lifecycle

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/seedlab` | Start SeedLab discovery run (background task) |
| GET | `/api/seedlab/{id}/logs` | Stream SeedLab logs |
| GET | `/api/strategies` | List all strategy versions |
| GET | `/api/strategies/{symbol}/v{ver}` | Get specific version |
| POST | `/api/strategies/{symbol}/v{ver}/evaluate` | Check promotion eligibility |
| POST | `/api/strategies/{symbol}/v{ver}/promote` | Promote to next stage |
| POST | `/api/strategies/{symbol}/v{ver}/activate` | Set as active live strategy |

### Hard Rules (13 checks, v1 parity)

1. confidence — min threshold
2. sl_tp_dir — SL/TP on correct sides
3. sl_distance — within asset min/max points
4. rr_ratio — minimum R:R
5. session — tradeable session score
6. spread — within spread limit
7. rsi_extreme — overbought/oversold
8. ema200_trend — trend alignment
9. news_blackout — high-impact news
10. tick_jump — 2-bar spike detection
11. liq_vacuum — thin-body spike candles
12. setup_type — blocked setup types
13. regime_block — dead market regime

### Risk Guards (7 stateful)

1. SignalHashFilter — duplicate signal dedup
2. ConfidenceVarianceFilter — unstable AI confidence
3. SpreadRegimeFilter — spread spike detection
4. EquityCurveScaler — halve risk below equity MA
5. DrawdownPauseGuard — pause on accelerating losses
6. NearDedupGuard — skip if open trade within N ATR
7. PortfolioCapGuard — block when total open risk exceeds cap
