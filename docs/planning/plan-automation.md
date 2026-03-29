# AlphaLoop v3 — Automation & Autonomous Development

## Purpose
Autonomous development system, scheduled tasks, backlog management, and CI gates.

---

## Autonomous Dev System

**Active Plan:** `.claude/plans/vectorized-stargazing-catmull.md`

### Concept
A scheduled hourly task runner that:
1. Reads `backlog.md` for the next task
2. Implements the task autonomously
3. Runs pytest as a quality gate
4. Auto-commits if tests pass
5. Moves to the next task

### Task Queue: `backlog.md`

**Format:**
```markdown
## Backlog

### Priority 1 (Critical)
- [ ] Fix CRIT-01: Entry point references non-existent cli function
- [ ] Fix CRIT-02: signal.SIGTERM crashes on Windows

### Priority 2 (High)
- [ ] Fix HIGH-01: getattr() on dict objects in trading/loop.py
- [ ] Add unit tests for seedlab/runner.py

### Priority 3 (Medium)
- [ ] Refactor settings page tab-switching to save on switch
- [ ] Add WebSocket reconnection dedup

### Completed
- [x] Fix BUG-001: Backtest infinite poll
- [x] Fix BUG-002: Resume restarts from scratch
```

### Pytest Gate

**Rule:** No auto-commit unless all tests pass.

```bash
# Gate command
pytest tests/ --tb=short -q

# On failure:
#   1. Log the failure
#   2. Skip the task
#   3. Move to next task
#   4. Notify (optional Telegram)
```

### Error Escalation

```
Task fails → retry once → if still fails:
  ├── Log failure to change-log.md
  ├── Mark task as blocked in backlog.md
  ├── Move to next task
  └── After 3 blocked tasks in a row: pause automation
```

---

## Scheduled Trading Tasks

### MetaLoop (In-App Automation)
- **Trigger:** Every `check_interval` closed trades (default: 20)
- **Action:** Health check → research if degrading → optimize → new version
- **File:** `src/alphaloop/trading/meta_loop.py`

### MicroLearner (In-App Automation)
- **Trigger:** Every `TradeClosed` event
- **Action:** Nudge SL/confidence params ±1% per trade
- **Guardrail:** ±5% max total drift from baseline
- **File:** `src/alphaloop/trading/micro_learner.py`

### Startup Self-Heal
- **Trigger:** Application boot
- **Action:** Seed setting defaults, mark stale backtest rows as paused
- **File:** `src/alphaloop/core/lifecycle.py`

---

## Future: CI/CD Pipeline

### Pre-Commit
```yaml
- pytest tests/unit/ --tb=short
- mypy src/ --strict
- ruff check src/
```

### Post-Push
```yaml
- pytest tests/ --cov=src/alphaloop
- Coverage check: fail if < 70% on core modules
- Build check: python -c "import alphaloop"
```

### Deployment
```yaml
- Run migrations: alembic upgrade head
- Restart service
- Health check: GET /health
- Smoke test: verify WebUI loads
```

---

## Scope Limits

What automation CAN do:
- Edit source files to fix bugs or add features
- Run tests
- Create commits (with pytest gate)
- Update documentation

What automation CANNOT do (requires human):
- Push to remote
- Run database migrations on production
- Modify `.env` or credentials
- Change CI/CD pipeline configuration
- Deploy to production
- Delete branches or force-push
