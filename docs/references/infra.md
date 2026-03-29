# AlphaLoop v3 — Infrastructure Reference

## Purpose
Database engines, async patterns, logging, and operational infrastructure.

---

## Database

### SQLite (Development)
- **Driver:** aiosqlite (async SQLite wrapper)
- **WAL mode:** Enabled for concurrent read/write
- **Location:** `alphaloop.db` in project root
- **Table creation:** Auto-created on startup if SQLite (no Alembic needed in dev)

### PostgreSQL (Production)
- **Driver:** asyncpg (native async PostgreSQL)
- **Connection pool:** configurable via `DB_POOL_SIZE` (default: 5)
- **Migration:** Alembic (`alembic upgrade head`)

### Engine Factory
**File:** `src/alphaloop/db/engine.py` (56 lines)

```python
create_db_engine(url: str, pool_size: int = 5, echo: bool = False) -> AsyncEngine
```
- SQLite: single connection, WAL journal mode
- PostgreSQL: connection pool with overflow

### Session Factory
**File:** `src/alphaloop/db/session.py` (35 lines)

```python
create_session_factory(engine: AsyncEngine) -> async_sessionmaker
```
- Used by repositories for typed async queries
- Background tasks access via `deps._get_session_factory()`

### Migrations
**Location:** `src/alphaloop/db/migrations/`
- `env.py` — Alembic async migration environment
- `versions/001_initial_schema.py` — Initial table creation

---

## Async Patterns

### MT5 Bridge
MetaTrader5 API is a synchronous C DLL. All calls wrapped:

```python
result = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, timeframe, 0, count)
```

**Used in:**
- `data/fetcher.py` — OHLCV data fetch
- `execution/mt5_executor.py` — order placement

### Optuna in Thread Pool
CPU-bound Optuna optimization must not block the event loop:

```python
# Each trial creates a fresh event loop in the thread
def run_on_train(params):
    return asyncio.run(engine.run(params))

# Entire optimizer runs in thread pool
result = await asyncio.to_thread(optimize, study, run_on_train, n_trials=30)
```

**Rule:** Never use `run_coroutine_threadsafe()` for blocking workloads — it starves the event loop.

### Background Tasks
Pattern used by `backtester/runner.py` and `seedlab/background_runner.py`:

```python
_tasks: dict[str, asyncio.Task]      # Handle tracking
_stop_flags: dict[str, bool]         # Graceful stop
_logs: dict[str, list[str]]          # Log buffers (max 500 lines)

# Spawn
task = asyncio.create_task(_run_backtest(run_id, params))
_tasks[run_id] = task

# Stop
_stop_flags[run_id] = True  # Task checks each iteration

# Cleanup
_tasks.pop(run_id, None)
_logs.pop(run_id, None)
```

---

## Logging

### structlog
**File:** `src/alphaloop/monitoring/logging.py`

- JSON structured output
- Context propagation (symbol, instance_id, trade_id)
- Configurable level via `LOG_LEVEL` env var or Settings > System

### Log Buffering (Background Tasks)
```python
_logs: dict[str, list[str]]  # Per run_id
_MAX_LOG_LINES = 500

def _log(run_id: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    buf = _logs[run_id]
    buf.append(f"[{ts}] {msg}")
    if len(buf) > _MAX_LOG_LINES:
        buf[:] = buf[-_MAX_LOG_LINES:]
```

Streamed to browser via `GET /api/backtests/{id}/logs?offset=N` (polled every 2s).

---

## Rate Limiting

**File:** `src/alphaloop/ai/rate_limiter.py` (84 lines)

```python
class AsyncRateLimiter:
    def __init__(self, max_calls: int = 10, window_seconds: float = 60.0)
    async def acquire(self, provider: str) -> None  # blocks if rate exceeded
```

- Sliding window per provider
- Default: 10 calls per 60 seconds
- Uses `asyncio.sleep()` to block until window opens

---

## Health & Monitoring

### Health Endpoints
- `GET /health` → `{status: "ok", version: "3.0.0"}`
- `GET /health/detailed` → component status, watchdog info

### Health Check Aggregator
**File:** `src/alphaloop/monitoring/health.py`
- Checks: DB connection, MT5 status, AI provider availability
- Component status: `ok`, `degraded`, `down`

### Watchdog
**File:** `src/alphaloop/monitoring/watchdog.py`
- Monitors component status periodically
- Updates health endpoint data

### Metrics Ring Buffer
**File:** `src/alphaloop/monitoring/metrics.py`
- In-memory ring buffer for recent metrics
- No external metrics service dependency

### Heartbeat
**File:** `src/alphaloop/trading/heartbeat.py` (34 lines)
- Writes periodic JSON file with timestamp and status
- For external monitoring tools (e.g., process managers, alerting)

---

## Encryption

**File:** `src/alphaloop/utils/crypto.py`
- Helper functions for encrypting/decrypting sensitive settings
- Used for API keys stored in DB

---

## AI Credit Tracking

**File:** `src/alphaloop/utils/credits.py`
- Tracks AI API usage per provider
- Useful for cost monitoring
