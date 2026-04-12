# AlphaLoop v3 — Claude Context

## Project
Python 3.11+ async trading system — FastAPI WebUI, MT5 execution, AI-driven signal pipeline, backtester (VBT), strategy versioning.

- **Entry:** `src/alphaloop/main.py`
- **WebUI:** `src/alphaloop/webui/` (FastAPI + vanilla JS SPA)
- **Trading loop:** `src/alphaloop/trading/loop.py`
- **Pipeline:** `src/alphaloop/pipeline/conviction.py`
- **Tests:** `tests/unit/` + `tests/integration/`
- **Dev server:** port 8090 (8080 is reserved on this machine)
- **Strategies:** `strategy_versions/*.json`

## Architecture Rules
- **Async-first:** All public methods `async def` unless pure computation
- **No singletons:** Use DI container (`core/container.py`)
- **Repository pattern:** No raw DB sessions in business logic
- **Pydantic v2:** All schemas, configs, API models
- **EventBus:** Decouple cross-cutting concerns
- **structlog** for logging — never `print()`
- `asyncio.to_thread()` for sync MT5/Optuna calls
- Import from `alphaloop.` — not relative imports

## Frontend
- Vanilla JS, ES modules, no build tooling
- Hash-based SPA routing (`#dashboard`, `#trades`, etc.)
- CSS variables for theming (gold `#EF9F27`, dark/light mode)
- `apiFetch()` wrapper for all HTTP calls
- `route-change` event cleanup for intervals/polls

## Testing
- `pytest-asyncio`, `asyncio_mode = "auto"`
- Mock MT5 and AI providers — never call real APIs in tests
- In-memory SQLite for DB tests
- Mirror source structure: `src/alphaloop/risk/sizer.py` → `tests/unit/test_position_sizer.py`

## Autonomy
**Can do freely:** edit `src/`, `tests/`, `docs/`; run `pytest`, `mypy`, linters.

**Ask first:** git commits/push, DB migrations, deleting files, changing `pyproject.toml`, running the app, any destructive git op.

## Key References

| Topic | File |
|-------|------|
| System architecture | `docs/references/architecture.md` |
| Trading modes | `docs/references/trading-modes.md` |
| Pipeline funnel | `docs/references/pipeline-funnel.md` |
| Trade decision logic | `docs/references/trade-decision.md` |
| Blocking policy | `docs/references/blocking-policy.md` |
| Observability | `docs/references/observability.md` |
| Change history | `docs/change-log.md` |
