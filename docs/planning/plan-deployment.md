# AlphaLoop v3 — Deployment & Operations

## Purpose
How to deploy, configure, and operate AlphaLoop v3.

---

## Local Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run with dry-run mode (no real trades)
python -m alphaloop --symbol XAUUSD --dry-run --port 8888

# WebUI only (no trading loop)
python -m alphaloop --web-only --port 8888

# Windows launcher
run.bat
```

**CLI flags:**
| Flag | Default | Description |
|------|---------|-------------|
| `--symbol` | `XAUUSD` | Trading symbol |
| `--dry-run` | `True` | No real orders |
| `--port` | `8888` | WebUI port |
| `--web-only` | `False` | Skip trading loop |
| `--poll-interval` | `300` | Seconds between cycles |
| `--log-level` | `INFO` | Logging verbosity |

---

## Environment Variables

All loaded via `src/alphaloop/core/config.py` (Pydantic BaseSettings).

### Broker (MT5)
| Key | Default | Description |
|-----|---------|-------------|
| `BROKER_SERVER` | `MetaQuotes-Demo` | MT5 server |
| `BROKER_LOGIN` | `0` | MT5 account login |
| `BROKER_PASSWORD` | `""` | MT5 password |
| `BROKER_TERMINAL_PATH` | `""` | Path to terminal64.exe |
| `BROKER_SYMBOL` | `XAUUSDm` | Default symbol |
| `BROKER_MAGIC` | `20240101` | EA magic number |

### API Keys
| Key | Description |
|-----|-------------|
| `GEMINI_API_KEY` | Google Gemini |
| `OPENAI_API_KEY` | OpenAI |
| `CLAUDE_API_KEY` | Anthropic Claude |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `XAI_API_KEY` | xAI Grok |
| `QWEN_API_KEY` | Qwen (Together AI) |

### Database
| Key | Default | Description |
|-----|---------|-------------|
| `DB_URL` | `sqlite+aiosqlite:///alphaloop.db` | Database URL |
| `DB_POOL_SIZE` | `5` | Connection pool size |

### Telegram
| Key | Description |
|-----|-------------|
| `TELEGRAM_TOKEN` | Bot token |
| `TELEGRAM_CHAT_ID` | Chat ID for notifications |

---

## Database Setup

### SQLite (Default, Development)
No setup needed. Database created automatically on first run at `alphaloop.db`.
Uses WAL mode for concurrent read/write.

### PostgreSQL (Production)
1. Create database: `CREATE DATABASE alphaloop;`
2. Set env var: `DB_URL=postgresql+asyncpg://user:pass@host:5432/alphaloop`
3. Install asyncpg: `pip install asyncpg`
4. Run migrations: `alembic upgrade head`

### Migrations
```bash
# Apply all migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Check current revision
alembic current
```

---

## v2 to v3 Migration

### Database Migration
```bash
# Preview changes (dry-run)
python scripts/migrate_v2_db.py --source /path/to/v2/alphaloop.db --dry-run

# Execute migration
python scripts/migrate_v2_db.py --source /path/to/v2/alphaloop.db
```

### Settings Migration
```bash
# Migrate 68 settings keys
python scripts/migrate_v2_settings.py --source /path/to/v2/alphaloop.db --dry-run
python scripts/migrate_v2_settings.py --source /path/to/v2/alphaloop.db
```

Covers: API keys, broker/MT5 credentials, AI models, risk params, signal thresholds, session windows, Telegram config, tool toggles, evolution guardrails, system config. Encrypted values transferred as-is.

---

## Startup Self-Heal

On server startup, `lifecycle.py` runs:
1. Initialize database connection
2. Create tables if SQLite (dev mode)
3. Seed 83+ setting defaults into DB (skips keys with existing values)

**Future:** Mark stale `state="running"` backtest rows as `"paused"` on boot to prevent infinite polling after crashes.

---

## WebUI Access

- URL: `http://localhost:{port}`
- Auth: Bearer token via `AUTH_TOKEN` env var or `WEBUI_TOKEN` in settings
- Dev mode: No token = all requests allowed
- WebSocket: `ws://localhost:{port}/ws?token={token}`

---

## Monitoring

### Health Endpoint
- `GET /health` — `{status, version}`
- `GET /health/detailed` — `{status, version, components: [...], watchdog: {...}}`

### Heartbeat
`trading/heartbeat.py` writes periodic JSON file for external monitoring tools.

### Logs
structlog JSON output. Configure via `LOG_LEVEL` env var or Settings > System > Log Level.
