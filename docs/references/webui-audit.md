# WebUI ↔ backend wiring audit (Gate-1)

**Purpose.** Catalogue every Settings toggle / threshold that persists to the
DB and check whether the running agent process actually re-reads it between
cycles. Toggles that don't round-trip are decorative until they do.

## How the runtime reloads strategy config

`TradingLoop._load_runtime_strategy()` (see `src/alphaloop/trading/loop.py:300-375`):

1. Calls `strategy_loader.load_active_strategy(self.settings_service, symbol, instance_id)` on each cycle.
2. Computes a JSON-sorted signature of the effective runtime config.
3. If the signature is unchanged, returns immediately — no rebuild.
4. On change, rebuilds `_algo_engine`, `_feature_pipeline`, `_execution_orch.runtime_strategy`.

**Consequence.** Any setting that flows *through* `load_active_strategy` into the active strategy JSON is live-synced on the next cycle (one `poll_interval` delay). Anything read from `app_settings` directly outside the strategy file is effectively cached at process start.

## Toggle-by-toggle status

Legend: ✅ live-synced each cycle · ⚠️ live-synced only if strategy JSON reflects it · ❌ read at startup only · ❓ not verified.

### Risk caps (`Settings → Risk`)
| Key | Status | Notes |
|---|---|---|
| `MAX_DAILY_LOSS_PCT` | ✅ | `RiskMonitor` reads on `can_open_trade()` from app_settings each call. |
| `MAX_CONCURRENT_TRADES` | ✅ | same. |
| `CONSECUTIVE_LOSS_LIMIT` | ✅ | |
| `RISK_PCT` | ⚠️ | read via `strategy.params.risk_pct`; only effective after strategy reload. |
| `LEVERAGE` | ❌ | read by `ExecutionService` once at startup. |
| `COMMISSION_PER_LOT` | ❌ | same. |

### Guardrails — MetaLoop (`Settings → Guardrails`)
| Key | Status | Notes |
|---|---|---|
| `METALOOP_ENABLED` | ✅ | `MetaLoop` polls DB on each research trigger. |
| `METALOOP_CHECK_INTERVAL` | ✅ | |
| `METALOOP_ROLLBACK_WINDOW` | ✅ | |
| `METALOOP_AUTO_ACTIVATE` | ⚠️ | honoured only at promotion decision time — verify the call site before relying on it in tests. |

### Promotion gates (`Settings → Promotion`)
| Key | Status | Notes |
|---|---|---|
| `PROMOTION_CANDIDATE_GATE_ALGO_ONLY` | ⚠️ | read by `strategies` route when computing eligibility, not by the running loop. |
| `PROMOTION_CANDIDATE_GATE_ALGO_AI` | ⚠️ | same. |
| `PROMOTION_CANDIDATE_GATE_AI_SIGNAL` | ⚠️ | same. |

### Validation thresholds (`Settings → Validation`)
| Key | Status | Notes |
|---|---|---|
| `MIN_CONFIDENCE` | ⚠️ | read through strategy params. |
| `CLAUDE_MIN_RR` | ⚠️ | flows into `strategy.validation` dict. |
| `MAX_VOLATILITY_ATR_PCT`, `MIN_VOLATILITY_ATR_PCT` | ⚠️ | same. |
| `CLAUDE_CHECK_H1_TREND`, `CLAUDE_CHECK_RSI`, `CLAUDE_CHECK_NEWS`, `CLAUDE_CHECK_SETUP` | ⚠️ | consumed inside `BoundedAIValidator` via strategy config; only effective after strategy reload. |

### Tools (`Settings → Tools`) — the big one
All `tool_enabled_*` toggles and the associated thresholds (`MIN_SESSION_SCORE`, `MAX_VOLATILITY_ATR_PCT`, `BOS_MIN_BREAK_ATR`, `FVG_MIN_SIZE_ATR`, …) are read on each cycle via `self._active_strategy_runtime()` → `runtime_strategy.get("tools")` in `_build_v4_orchestrator()` and its callees.

**Status:** ✅ effective per cycle **as long as** the strategy JSON's `tools` section reflects the settings. The write path `SettingsRepository.set_many()` updates the `app_settings` table, but `load_active_strategy` pulls tool overrides from the strategy JSON file, **not** from `app_settings`. This is the biggest round-trip risk.

**Action (deferred to Gate-2):** either add a `SettingsChanged` pub-sub so the loop re-loads tool overrides directly from `app_settings` on the next cycle, or rewrite the active strategy JSON file whenever tool settings change. Decision deferred until the Gate-1 funnel tells us whether any of these toggles are the actual choke point.

### Session / News
| Key | Status | Notes |
|---|---|---|
| `SESSION_*_OPEN/CLOSE` | ✅ | `SessionFilter` reads DB each call. |
| `NEWS_PRE_MINUTES`, `NEWS_POST_MINUTES` | ✅ | same. |
| `NEWS_PROVIDER` | ❌ | resolved once at provider init. |

### System
| Key | Status | Notes |
|---|---|---|
| `DRY_RUN` | ❌ | resolved at loop start from env + DB. Changing it mid-session requires a restart. |
| `LOG_LEVEL` | ❌ | applied at `setup_logging()`. |
| `CONFIDENCE_SIZE_ENABLED` | ⚠️ | strategy param. |
| `MICRO_LEARN_ENABLED` | ⚠️ | strategy param. |

### Telegram
| Key | Status | Notes |
|---|---|---|
| `TELEGRAM_ENABLED` | ✅ | checked per notification. |

## Controls endpoints — UI exposure

| Endpoint | Wired in UI? | Status |
|---|---|---|
| `POST /api/controls/incidents/{id}/ack` | ✅ | Gate-1: added to Risk Dashboard → "Incidents & Risk Lock" card. |
| `POST /api/controls/no-new-risk/clear` | ✅ | Gate-1: added to same card. Only enabled when `compound_clearable`. |
| `GET /api/controls/risk-state` | ✅ | Gate-1. |
| `GET /api/controls/guards-status` | ✅ | Gate-1: new endpoint + UI card on Observability. |
| `GET /api/controls/portfolio` | ⚠️ | exposed on Risk dashboard only indirectly via `/api/risk/portfolio`. |

## Decision panel → backend verification

The `TradeDecision` shown in the observability UI is authoritative for the cycle that produced it. It is a **projection** of the `PipelineResult` at `_finalise()` time; no downstream code reads it. This means the UI can never show a trade as "rejected at stage X" when it was actually executed — if the cycle reached the broker, `execution_status = "executed"`.

## Open questions (deferred to Gate-2)

1. Add a `SettingsChanged` event so tool toggles live-sync into running agents without a restart.
2. Expose `/api/controls/portfolio` and `/api/controls/guards-status` on the Agents tab per-instance view so each running loop shows its own drawdown-pause state.
3. Remove the decorative `METALOOP_AUTO_ACTIVATE` toggle or wire it explicitly into the promotion call site.
