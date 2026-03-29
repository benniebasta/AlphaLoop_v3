# AlphaLoop v3 — Test Strategy

## Purpose
Test strategy, pytest setup, coverage targets, and known gaps.

---

## Current Test Coverage

| Category | Count | Location |
|----------|-------|----------|
| Unit | 20+ | `tests/unit/` |
| Integration | 4+ | `tests/integration/` |
| E2E | 1 | `tests/e2e/` |

### Unit Tests
| File | Covers |
|------|--------|
| `test_config.py` | Pydantic config loading, hard caps |
| `test_signal_schema.py` | TradeSignal/ValidatedSignal validation |
| `test_hard_rules.py` | All 13 hard rule checks |
| `test_indicators.py` | RSI, EMA, ATR, VWAP, MACD, Bollinger, ADX |
| `test_position_sizer.py` | Position sizing math |
| `test_risk_monitor.py` | Kill switch, daily limits |
| `test_risk_guards.py` | All 7 stateful guards |
| `test_circuit_breaker.py` | Failure tracking, is_open |
| `test_event_bus.py` | Pub/sub, handler isolation |
| `test_tools_pipeline.py` | Pipeline short-circuit, registry |
| `test_ai_caller.py` | Provider routing, rate limiting |
| `test_assets.py` | Asset catalog, yfinance mapping |
| `test_atr_calculation.py` | ATR computation accuracy |
| `test_session_time.py` | Session score calculation |
| `test_strategy_params.py` | StrategyParams validation |
| `test_cli.py` | CLI argument parsing |
| `test_health.py` | Health check aggregation |
| `test_guard_timeout.py` | Guard timeout behavior |
| `test_new_modules.py` | MetaLoop, HealthMonitor, MicroLearner |

### Integration Tests
| File | Covers |
|------|--------|
| `test_db.py` | Engine creation, table creation |
| `test_repositories.py` | CRUD operations via repositories |
| `test_health.py` | Health endpoint with real DB |
| `test_webui_routes.py` | API endpoint responses |

### E2E Tests
| File | Covers |
|------|--------|
| `test_lifecycle.py` | Boot → run 1 cycle → shutdown |

---

## Pytest Configuration

**Location:** `pyproject.toml`

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Shared Fixtures:** `tests/conftest.py`
- In-memory SQLite: `sqlite+aiosqlite:///:memory:`
- Auto-creates all tables
- Provides: `db_engine`, `db_session`, `container`, `app_config`

---

## Key Test Patterns

### Mock MT5
MT5 is Windows-only and requires a running terminal. All tests mock MT5 calls:
```python
with patch("alphaloop.data.fetcher.mt5") as mock_mt5:
    mock_mt5.copy_rates_from_pos.return_value = fake_ohlcv_data
```

### Mock AI Providers
```python
with patch.object(AICaller, "call_model", return_value='{"direction": "BUY", ...}'):
    signal = await engine.generate_signal(context, params)
```

### In-Memory DB
All DB tests use in-memory SQLite via `conftest.py`. No cleanup needed — DB dies with the test session.

### Async Tests
All test files use `pytest-asyncio` with `asyncio_mode = "auto"`. Test functions are plain `async def`.

---

## Coverage Targets

| Module | Target | Priority |
|--------|--------|----------|
| `core/` | 90%+ | High |
| `signals/` | 85%+ | High |
| `validation/` | 90%+ | High |
| `risk/` | 90%+ | High |
| `trading/` | 80%+ | High |
| `data/` | 75%+ | Medium |
| `ai/` | 70%+ | Medium |
| `tools/` | 70%+ | Medium |
| `webui/routes/` | 60%+ | Medium |
| `seedlab/` | 50%+ | Low |
| `backtester/` | 50%+ | Low |
| `research/` | 50%+ | Low |

**Run coverage:**
```bash
pytest --cov=src/alphaloop --cov-report=html tests/
```

---

## Known Gaps (Phase 9)

| Area | Missing | Notes |
|------|---------|-------|
| `seedlab/` | Pipeline orchestration tests | Needs mock regime detection + backtest engine |
| `backtester/` | Optuna integration tests | Needs mock engine, checkpoint save/load |
| `research/` | Analyzer + applier tests | Needs mock trade data + AI caller |
| `trading/loop.py` | Full cycle integration test | Needs all components mocked or in-memory |
| `webui/routes/strategies.py` | Promote/activate endpoint tests | Needs mock strategy files |
| `webui/routes/live.py` | Live data endpoint tests | Needs mock MT5 position data |
| Migration scripts | End-to-end migration test | Needs v2 fixture database |

---

## Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# Specific test file
pytest tests/unit/test_hard_rules.py -v

# With coverage
pytest --cov=src/alphaloop tests/

# Type checking
mypy src/ --strict
```
