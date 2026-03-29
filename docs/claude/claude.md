# AlphaLoop v3 — AI Orchestration & Autonomy Rules

## Purpose
Global AI orchestration strategy, autonomy boundaries, and coding standards for Claude sessions working on this project.

---

## Priority System

When multiple tasks are available, prioritize in this order:

1. **Critical bugs** — runtime crashes, data corruption, security issues
2. **Active plan tasks** — items from the current `.claude/plans/` plan file
3. **Backlog items** — from `backlog.md`, by priority level
4. **Test coverage** — missing tests for critical paths
5. **Code quality** — refactoring, dead code cleanup, type annotations

---

## Autonomy Rules

### Can Do Without Confirmation
- Edit source files in `src/alphaloop/`
- Edit test files in `tests/`
- Run `pytest` (read-only validation)
- Run `mypy` or linting tools
- Read any file in the project
- Create new test files
- Update documentation in `docs/`

### Requires Confirmation
- Git commits (always ask or wait for user request)
- Git push to remote
- Database migrations (`alembic revision`, `alembic upgrade`)
- Deleting files (unless clearly dead code confirmed by grep)
- Modifying `pyproject.toml` dependencies
- Changing `.env` or credential files
- Running the application (`python -m alphaloop`)
- Any destructive git operation (reset, force-push, branch delete)

---

## Coding Standards

### Architecture
- **Async-first:** All public methods are `async def` unless pure computation
- **No singletons:** Use DI container (`core/container.py`)
- **Repository pattern:** No raw DB sessions in business logic
- **Pydantic v2:** All schemas, configs, and API models
- **EventBus:** Decouple cross-cutting concerns (notifications, metrics, WebSocket)

### Python
- Python 3.11+ features allowed (StrEnum, `X | Y` union syntax, `match/case`)
- Type hints on all function signatures
- structlog for logging (not `print()`)
- `asyncio.to_thread()` for sync MT5/Optuna calls — never `run_coroutine_threadsafe()`
- Import from `alphaloop.` not relative imports

### Frontend
- Vanilla JS, ES modules, no build tooling
- Hash-based SPA routing (`#dashboard`, `#trades`, etc.)
- CSS variables for theming (gold `#EF9F27`, dark/light mode)
- `apiFetch()` wrapper for all HTTP calls (handles auth token)
- `route-change` event cleanup for intervals/polls

### Testing
- `pytest-asyncio` with `asyncio_mode = "auto"`
- Mock MT5 and AI providers — never call real APIs in tests
- In-memory SQLite for DB tests
- Test files mirror source structure: `src/alphaloop/risk/sizer.py` → `tests/unit/test_position_sizer.py`

---

## Module Ownership

### Claude Managed (full autonomy)
- `tests/` — write and update tests freely
- `docs/` — update documentation
- `src/alphaloop/core/` — framework code
- `src/alphaloop/signals/` — signal generation
- `src/alphaloop/validation/` — signal validation
- `src/alphaloop/tools/` — filter plugins
- `src/alphaloop/monitoring/` — observability
- `src/alphaloop/webui/static/` — frontend JS/CSS

### Requires Extra Care
- `src/alphaloop/risk/` — position sizing math must be exact
- `src/alphaloop/execution/` — real money operations
- `src/alphaloop/trading/loop.py` — core trading cycle
- `src/alphaloop/db/models/` — schema changes need migration
- `src/alphaloop/core/config.py` — settings affect all modules

---

## Key References

| Topic | File |
|-------|------|
| Master roadmap | `docs/planning/plan.md` |
| System architecture | `docs/planning/plan-architecture.md` |
| Data flow | `docs/planning/plan-dataflow.md` |
| UI architecture | `docs/planning/plan-ui.md` |
| Backend modules | `docs/planning/plan-backend.md` |
| All agents | `docs/claude/claude-agents.md` |
| All pipelines | `docs/claude/claude-pipelines.md` |
| AI integration | `docs/claude/claude-ai-integration.md` |
| Change history | `docs/change-log.md` |
