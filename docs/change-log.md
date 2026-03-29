# AlphaLoop v3 — Change Log

Timestamped record of major changes, bug postmortems, and architectural decisions.

---

## 2026-03-29 — G.4-G.9 New Features Complete

**241 tests passing, 0 failures.**

### New Backend Modules (6 files)
- `trading/portfolio_manager.py` — Multi-symbol portfolio coordination (max positions, heat cap, cross-symbol tracking)
- `data/live_feed.py` — Real-time price streaming (MT5 primary, yfinance fallback, tick caching)
- `research/report_generator.py` — Automated daily/weekly/monthly performance reports with Telegram formatting
- `monitoring/alert_rules.py` — Configurable alert rules engine (5 default rules: daily loss, consecutive losses, portfolio heat, circuit breaker, spread spike)
- `core/feature_flags.py` — Runtime feature toggle system (10 default flags, DB sync, runtime overrides)
- `webui/routes/event_log.py` + `webui/routes/risk_dashboard.py` — New API routes (created earlier)

### New Frontend Pages (3 files)
- `components/health.js` — System health dashboard (component status cards, watchdog info, auto-refresh 15s)
- `components/event_log.js` — Live event log viewer (type filter badges, auto-refresh 5s)
- `components/risk_dashboard.js` — Risk monitor dashboard (daily P&L, consecutive losses, win rate bar, heat meter)

### Navigation Updates
- Added "Risk & Monitoring" nav group with Risk, Event Log, Health pages
- SPA router updated for 3 new routes

### WebSocket Event Handlers (app.js)
- Central handler for all 13 event types with toast notifications
- Toast CSS: added `info` and `warning` border colors

### Test Coverage
- 4 new test files: test_portfolio_manager.py (6), test_feature_flags.py (5), test_alert_rules.py (6), test_report_generator.py (3)
- Total: 241 tests (up from 221)

---

## 2026-03-29 — Full Audit Fixes Complete (67 fixes, 50+ files)

All CRITICAL (5), HIGH (21), MEDIUM (44) audit items fixed. 7 new test files added (55 tests).

Key fixes:
- CLI entry point, SIGTERM Windows, missing methods, asyncio patterns
- Trading guards fixed (getattr-on-dict, circuit breaker kill, spread data)
- Live page wired with yfinance data + EMA/RSI chart overlays
- Settings tab data persistence, WebSocket subscription leak
- AI Hub role key alignment, signal_mode in backtests
- Backtester ATR alignment, TP1/TP2 order, session filter timestamps
- Schema drift fixed (String widths + missing columns)
- Monte Carlo thread offloaded, API error bodies preserved
- 15 unused imports removed, dead code cleaned up

---

## 2026-03-29 — Full Codebase Audit

**Auditor:** Claude Opus 4.6
**Scope:** 188 Python files, 14 JS files, 1 CSS file, tests

| Severity | Count |
|----------|-------|
| CRITICAL | 5 |
| HIGH | 21 |
| MEDIUM | 44 |
| LOW | 80+ |

### CRIT-01: Entry point references non-existent `cli` function
- `pyproject.toml:57` — `alphaloop.main:cli` but `main.py` has no `cli()` function
- **Fix:** Create `cli()` wrapper or change entry point to `alphaloop.main:main`

### CRIT-02: `signal.SIGTERM` crashes on Windows
- `main.py:92` — Windows does not support `SIGTERM` via `signal.signal()`
- **Fix:** Wrap in `try/except OSError` or platform check

### CRIT-03: `repo.get_closed()` does not exist
- `research/analyzer.py:341` — `TradeRepository` only has `get_closed_trades()`
- **Fix:** Change to `repo.get_closed_trades()`

### CRIT-04: `asyncio.run()` inside running event loop
- `backtester/asset_trainer.py:268` — nested `asyncio.run()` raises `RuntimeError`
- **Fix:** Use `await` directly or `asyncio.to_thread()`

### CRIT-05: Migration script uses wrong column names
- `scripts/migrate_v2_db.py:81-93` — INSERT references columns not in v3 schema
- **Fix:** Rewrite to match v3 column names

### Key Audit Findings
- Trading guards silently disabled (`getattr()` on dicts always returns default)
- Live Trading Monitor returns hardcoded None/empty for all data
- Settings page loses edits when switching tabs before saving
- WebSocket event handler leaks on reconnection
- Backtester ATR computed with off-by-one alignment error
- Schema drift: ORM models define `String(32)` but migration creates `String(16)`

Full audit: see `AUDIT_AND_IMPLEMENTATION_PLAN.md` (archived)

---

## 2026-03-29 — BUG-001: Backtest Infinite Poll (FIXED)

**Symptom:** Backtest page stuck, hammering `/api/backtests` every second.
**Root cause:** Backtest process killed mid-run without updating DB state. Row stayed at `state="running"` forever. UI polls while `state == "running"`.
**Fix:** `PATCH /api/backtests/{run_id}/stop` now force-sets `state = "paused"` when `request_stop()` returns False (task not in memory).
**Prevention:** On server startup, mark stale `state="running"` rows as `"paused"`.

---

## 2026-03-29 — BUG-002: Resume Restarts Backtest from Scratch (FIXED)

**Symptom:** Stopping a backtest mid-run and resuming restarts from generation 1.
**Root cause:** `resume_backtest` didn't pass `timeframe` or `tools` — both defaulted wrong — causing `data_hash` mismatch — checkpoint discarded.
**Secondary:** `tools_json` was never saved to DB on creation.
**Fix:**
- `create_backtest` now saves `tools_json` to DB
- `resume_backtest` reads `run.timeframe` and `run.tools_json` from DB and passes them

---

## 2026-03-28 — WebUI Makeover Complete

- Primary color: `#EF9F27` (gold), light mode variant `#d48b1a`
- Dark + light mode toggle (localStorage, no DB round-trip)
- 10-tab Flaticon+emoji navigation in 4 groups
- Live Trading Monitor with Lightweight Charts v4.2 (ported from v1)
- SeedLab rename with signal mode toggle
- Alpha Agents with WebUI Deploy/Stop subprocess management
- Strategy status pill tabs with lifecycle visualization
- 22+ missing setting defaults seeded on startup
- Agent registration bug fixed
- 20 files modified/created

---

## 2026-03-20 — Phases 0-8 Complete

All core phases of the v3 rewrite completed:
- Phase 0: Scaffolding (config, DB, health endpoint)
- Phase 1: Domain models & schemas (7 DB models, repositories)
- Phase 2: AI layer (4 providers: Anthropic, Gemini, OpenAI-compat, Ollama)
- Phase 3: Data layer + tool plugins (10 tools, plugin auto-discovery)
- Phase 4: Signal + validation + risk (13 hard rules, 7 risk guards)
- Phase 5: Trading loop (strategy loader, meta-loop, health monitor, micro-learner)
- Phase 6: WebUI (14 route modules, 10-tab SPA, WebSocket live updates)
- Phase 7: Research + SeedLab + Backtester (Optuna optimization, deployment pipeline)
- Phase 8: Monitoring (structlog, health checks, watchdog)

Phase 9 (Testing + Hardening) in progress.
