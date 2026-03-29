# AlphaLoop v3 — Alpha Agents Reference

## Purpose
Alpha Agents subprocess lifecycle — how trading agents are launched, managed, and stopped via the WebUI.

---

## Overview

Alpha Agents are independent Python subprocesses, each running a `TradingLoop` for a specific symbol. They are managed through the WebUI "Alpha Agents" tab (route: `#agents`, alias: `#bots`).

---

## Lifecycle

### Launch
**Endpoint:** `POST /api/bots/start`

```
WebUI "Deploy" button
  → POST /api/bots/start {symbol, flags}
  → subprocess.Popen(["python", "-m", "alphaloop", "--symbol", symbol, ...])
  → Register in DB: RunningInstance {symbol, instance_id, pid, started_at}
  → Return: {instance_id, pid, status}
```

**Process:** Each agent runs as an independent Python process with its own event loop, DB connection, and trading loop.

### Stop
**Endpoint:** `POST /api/bots/{instance_id}/stop`

```
WebUI "Stop" button
  → POST /api/bots/{instance_id}/stop
  → On Unix: os.kill(pid, signal.SIGTERM) → graceful shutdown
  → On Windows: TerminateProcess (hard kill — no cleanup)
  → Always delete DB record (RunningInstance)
  → Return: {status: "stopped"}
```

**Windows caveat:** `TerminateProcess` is a hard kill. The agent cannot self-unregister or run shutdown hooks. The WebUI always deletes the DB record on stop regardless.

### List & Auto-Purge
**Endpoint:** `GET /api/bots`

```
→ Query all RunningInstance records from DB
→ For each: check _pid_alive(pid)
   ├── If alive: include in response
   └── If dead: auto-delete DB record (stale entry from crash/restart)
→ Return: {bots: [{id, symbol, instance_id, pid, started_at, strategy_version}]}
```

---

## Database Model

**File:** `src/alphaloop/db/models/instance.py` (28 lines)

```python
class RunningInstance(Base):
    __tablename__ = "running_instances"
    id: int               # Primary key
    symbol: str           # Trading symbol (e.g., "XAUUSD")
    instance_id: str      # Unique instance identifier
    pid: int              # OS process ID
    started_at: datetime  # Launch timestamp
    strategy_version: str # Active strategy version
```

---

## WebUI Agent Cards

Each running agent displays a card with:
- Green pulse dot (alive indicator)
- Symbol (large, prominent)
- Strategy version
- Uptime (calculated from `started_at`: Xd Xh / Xh Xm / Xm / Xs)
- PID (process ID)
- Instance ID
- Started timestamp
- Red "Remove" button (confirmation dialog)

Auto-refresh every 30 seconds via `setInterval`.

---

## Route Files

**Backend:** `src/alphaloop/webui/routes/bots.py` (84 lines)
- `GET /api/bots` — list with auto-purge
- `POST /api/bots` — register (manual)
- `POST /api/bots/start` — deploy subprocess
- `POST /api/bots/{instance_id}/stop` — stop + delete
- `DELETE /api/bots/{instance_id}` — remove record

**Frontend:** `src/alphaloop/webui/static/js/components/bots.js`
- Card grid layout
- Uptime calculation
- Auto-refresh with `route-change` cleanup
- Delete confirmation dialog

---

## Multi-Instance Safety

- `RunningInstance` model prevents duplicate registrations
- Each agent uses a unique `instance_id`
- `_pid_alive()` check on every list request cleans up orphans
- PID check: `os.kill(pid, 0)` on Unix, `OpenProcess` on Windows
