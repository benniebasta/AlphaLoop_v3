# AlphaLoop v3 — Context & State Management

## Purpose
How the system maintains state, configuration, and session context.

---

## Configuration Priority Chain

```
1. Runtime overrides (ConfigChanged events)    ← highest priority
2. Database (app_settings table, via WebUI)
3. Environment variables / .env file
4. Hardcoded defaults in core/config.py        ← lowest priority
```

**Implementation:** `src/alphaloop/config/settings_service.py`

At startup:
1. `AppConfig` (Pydantic BaseSettings) loads from `.env` → environment → defaults
2. `SettingsService.seed_defaults(SETTING_DEFAULTS)` fills absent DB keys
3. On WebUI save: `SettingsService.set_many(settings)` writes to DB
4. On next read: DB values override env/defaults

---

## Setting Categories

**File:** `src/alphaloop/core/lifecycle.py` seeds these on startup:

| Category | Count | Examples |
|----------|-------|---------|
| Signal | 25 | `min_confidence`, `min_rr_ratio`, `rsi_ob`, `rsi_os` |
| Tools | 43 | `use_session_filter`, `bos_min_break_atr`, `dd_pause_duration` |
| MetaLoop | 5 | `metaloop_enabled`, `metaloop_check_interval` |
| Health | 6 | `health_w_sharpe`, `health_healthy_threshold` |
| Confidence | 1 | `confidence_sizing_enabled` |
| Micro-learning | 3 | `micro_learning_enabled`, `micro_max_nudge_pct` |
| **Total** | **83+** | |

---

## Strategy State in DB

Active strategy per symbol stored as DB setting:

```
Key: active_strategy_{symbol}
Value: JSON string with version, params, tool config, AI model overrides
```

**Read by:** `trading/strategy_loader.py` → `load_active_strategy(symbol)`
**Written by:** `POST /api/strategies/{symbol}/v{ver}/activate`

### Strategy Version Files

JSON files in `strategy_versions/{SYMBOL}_v{N}.json`:
- Strategy parameters (EMA periods, SL/TP multipliers, etc.)
- Tool configuration (which filters enabled)
- AI model overrides (signal, validator, research, autolearn)
- Performance metrics (Sharpe, win rate, max drawdown)
- Status: `candidate` → `dry_run` → `demo` → `live`

---

## Micro-Adjustment State

Per-symbol micro-learner adjustments stored in DB:

```
Key: micro_adjustments_{symbol}
Value: JSON with param nudges relative to baseline
```

- Accumulates over trades (±1% per trade, ±5% total cap)
- Reset when full MetaLoop autolearn runs
- Merged on top of strategy params at load time

---

## In-Memory State

### Background Tasks
```python
# backtester/runner.py & seedlab/background_runner.py
_tasks: dict[str, asyncio.Task]     # Running task handles
_stop_flags: dict[str, bool]        # Graceful stop signals
_logs: dict[str, list[str]]         # Log buffers (max 500 lines)
```

### Risk Guards (Stateful)
```python
# risk/guards.py — each guard maintains rolling state
SignalHashFilter._recent_hashes: deque       # Last 3 signal hashes
ConfidenceVarianceFilter._confidences: deque  # Last 3 confidence values
SpreadRegimeFilter._spreads: deque           # Last 50 spreads
EquityCurveScaler._equity_curve: deque       # Last 20 equity points
DrawdownPauseGuard._losses: deque            # Recent loss streak
```

### WebSocket Clients
```python
# webui/routes/websocket.py
_connections: set[WebSocket]  # Active WS connections
```

---

## Session Lifecycle

### Startup (`lifecycle.py:startup`)
1. `container.init_db()` — create DB engine + session factory
2. Create tables if SQLite (dev mode)
3. `SettingsService.seed_defaults(SETTING_DEFAULTS)` — fill 83+ default keys

### Runtime
- `TradingLoop` polls every `poll_interval` seconds
- `MetaLoop` listens for `TradeClosed` events in background
- `MicroLearner` nudges params per trade
- `WebSocket` broadcasts all events to browser clients
- `Heartbeat` writes periodic JSON file

### Shutdown (`lifecycle.py:shutdown`)
1. `container.close()` — dispose DB engine
2. Cancel background tasks
3. Close WebSocket connections

---

## Claude Memory Integration

**Location:** `C:\Users\benz-\.claude\projects\C--Users-benz--Documents-alphaloop-v3\memory\`

Memory files track:
- Project context (architecture decisions, completed phases)
- Active plans and their status
- Known bug fixes and their resolutions
- UI facts (theme colors, nav structure, route aliases)

**Not in memory** (derive from code):
- File paths, function names, module structure
- Git history, recent changes
- Current settings values
- Test results
