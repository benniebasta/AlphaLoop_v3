# AlphaLoop v3 — System Architecture

## Purpose
System architecture, component diagram, DI wiring, and service boundaries.

---

## Component Diagram

```
                    ┌─────────────────────────────────────────────────┐
                    │                   CLI / Main                     │
                    │           src/alphaloop/main.py                  │
                    └───────────────────┬─────────────────────────────┘
                                        │
                                        v
                    ┌─────────────────────────────────────────────────┐
                    │              Application Factory                │
                    │            src/alphaloop/app.py                 │
                    │      create_app() → Container                   │
                    └───────────────────┬─────────────────────────────┘
                                        │
                    ┌───────────────────┴─────────────────────────────┐
                    │              DI Container                       │
                    │         core/container.py                       │
                    │                                                 │
                    │  ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
                    │  │AppConfig │ │ EventBus │ │ DB Engine +    │  │
                    │  │(Pydantic)│ │ (pub/sub)│ │ SessionFactory │  │
                    │  └──────────┘ └──────────┘ └────────────────┘  │
                    └─────────────────────────────────────────────────┘
                                        │
                    ┌───────────┬───────┴───────┬────────────┬────────────┐
                    │           │               │            │            │
                    v           v               v            v            v
              ┌──────────┐ ┌────────┐ ┌──────────────┐ ┌─────────┐ ┌──────────┐
              │ Trading  │ │ WebUI  │ │  Backtester   │ │ SeedLab │ │ Research │
              │  Loop    │ │(FastAPI│ │  + Optuna     │ │ Runner  │ │ Analyzer │
              │          │ │  SPA)  │ │               │ │         │ │          │
              └────┬─────┘ └───┬────┘ └──────┬───────┘ └────┬────┘ └────┬─────┘
                   │           │             │              │            │
          ┌────────┴───────────┴─────────────┴──────────────┴────────────┘
          │
          v
    ┌──────────────────────────────────────────────────────────────────────────┐
    │                          Shared Services                                │
    │                                                                          │
    │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ ┌──────────────────┐   │
    │  │  AI     │ │  Signal  │ │ Validate │ │ Risk │ │  Data / Market   │   │
    │  │ Caller  │ │  Engine  │ │          │ │      │ │  Context         │   │
    │  └─────────┘ └──────────┘ └──────────┘ └──────┘ └──────────────────┘   │
    │                                                                          │
    │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ ┌──────────────────┐   │
    │  │  Tool   │ │ MT5      │ │ Telegram │ │ DB   │ │  Settings        │   │
    │  │Pipeline │ │ Executor │ │ Notifier │ │ Repos│ │  Service         │   │
    │  └─────────┘ └──────────┘ └──────────┘ └──────┘ └──────────────────┘   │
    └──────────────────────────────────────────────────────────────────────────┘
```

---

## DI Container Wiring

**File:** `src/alphaloop/core/container.py` (45 lines)

```
Container
  ├── config: AppConfig           # Pydantic BaseSettings (env + .env)
  ├── event_bus: EventBus         # Async pub/sub
  ├── db_engine: AsyncEngine      # SQLAlchemy async engine
  └── db_session_factory          # async_sessionmaker
```

**Initialization chain:**
1. `AppConfig` loads from `.env` → environment → defaults
2. `Container(config)` creates `EventBus`
3. `container.init_db()` creates engine + session factory
4. `startup(container)` seeds setting defaults into DB

---

## Async Event Bus Topology

**File:** `src/alphaloop/core/events.py` (194 lines)

**Publishers:**
| Component | Events Published |
|-----------|-----------------|
| `TradingLoop` | `SignalGenerated`, `SignalValidated`, `SignalRejected`, `TradeOpened`, `PipelineBlocked` |
| `MT5Executor` | `TradeClosed` |
| `MetaLoop` | `MetaLoopCompleted`, `StrategyRolledBack` |
| `SettingsService` | `ConfigChanged` |
| `DeploymentPipeline` | `StrategyPromoted` |
| `SeedLabRunner` | `SeedLabProgress` |
| `CanaryManager` | `CanaryStarted`, `CanaryEnded` |
| `RiskMonitor` | `RiskLimitHit` |
| `ResearchAnalyzer` | `ResearchCompleted` |

**Subscribers:**
| Subscriber | Listens To |
|------------|-----------|
| `websocket.py` | All events → broadcast to browser |
| `Telegram dispatcher` | `TradeOpened`, `TradeClosed`, `RiskLimitHit` |
| `MetaLoop` | `TradeClosed` |
| `MicroLearner` | `TradeClosed` |
| `HealthMonitor` | `TradeClosed` |
| `Metrics ring buffer` | All events |

---

## Plugin Discovery

**File:** `src/alphaloop/tools/registry.py`

Auto-discovers tool plugins from `tools/plugins/*/tool.py`:
1. Scans `tools/plugins/` for directories containing `tool.py`
2. Each `tool.py` exports a class inheriting `BaseTool`
3. `BaseTool` ABC enforces async `run(context) -> ToolResult`
4. `FilterPipeline` chains tools, short-circuits on first block

**10 plugins:** session_filter, news_filter, volatility_filter, dxy_filter, sentiment_filter, risk_filter, bos_guard, fvg_guard, vwap_guard, correlation_guard

---

## Repository Pattern

**Location:** `src/alphaloop/db/repositories/`

| Repository | Model(s) | Key Methods |
|------------|---------|-------------|
| `SettingsRepo` | `AppSetting` | `get`, `get_all`, `set`, `set_many`, `delete` |
| `TradeRepo` | `TradeLog`, `TradeAuditLog` | `create`, `get_open`, `get_closed_trades`, `count_by_outcome` |
| `ResearchRepo` | `ResearchReport`, `ParameterSnapshot`, `EvolutionEvent` | reports, snapshots, evolution events |
| `BacktestRepo` | `BacktestRun` | `create`, `get_by_run_id`, `update_state`, `update_progress` |
| `StrategyRepo` | `StrategyVersion` | strategy version CRUD |

**Rule:** No raw sessions in business logic — all queries go through repositories.

---

## MT5 Bridge Pattern

**Problem:** MetaTrader5 API is synchronous (C DLL).
**Solution:** Wrap all MT5 calls in `asyncio.to_thread()`.

```
Async code → asyncio.to_thread(mt5_sync_call) → MT5 C DLL → result
```

**Files:**
- `src/alphaloop/data/fetcher.py` — OHLCV fetch via `to_thread`
- `src/alphaloop/execution/mt5_executor.py` — order placement via `to_thread`

---

## Import Boundaries

No circular dependencies. Module import direction:

```
core/ ← (imported by everything)
  ↓
db/ ← config/ ← ai/ ← data/
  ↓
signals/ ← validation/ ← risk/ ← tools/
  ↓
trading/ ← execution/
  ↓
webui/ (imports from all above)
research/ ← seedlab/ ← backtester/
monitoring/ ← notifications/
```

**Rule:** Lower layers never import from upper layers. Cross-cutting communication uses the EventBus.
