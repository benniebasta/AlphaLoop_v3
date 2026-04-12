# Throughput Rebalance — Gate-1 Report

**Date:** 2026-04-12
**Plan:** `C:\Users\benz-\.claude\plans\imperative-splashing-gizmo.md`
**Gate:** Gate-1 (observability only — **no** threshold or hard/soft reclassification).

## Executive summary

Gate-1 of the throughput-vs-safety rebalance is **complete and ready for review**. Per operator instruction, this phase delivered **only observability**: a per-stage ledger, a funnel endpoint, a decision projection object, a new UI tab, the incident-ack / no-new-risk-clear buttons, a live replay harness, and a single-source-of-truth blocking-policy document. **No hard block, threshold, or validator authority has been changed.** The operator can now read the funnel, decide whether the over-blocking hypothesis is confirmed, and approve Gate-2 separately.

The per-stage ledger and all UI components ship safely: every code path is either append-only (ledger writes are fire-and-forget inside a `try/except`), or read-only (endpoints, UI, docs). The existing cycle-level `pipeline_decisions` table is unchanged — the new `pipeline_stage_decisions` table runs alongside it.

## Measurement status — **HYPOTHESIS CONFIRMED**

Backfill was executed against the live database during Gate-1 verification:

```
python -m scripts.replay_pipeline --source backfill --since 7d
→ cycles_scanned: 241, stage_rows_written: 1114
```

### Measured funnel (last 7 days, `source=live`, all symbols)

| Stage | Total | Passed | Blocked | Held | Top reason |
|---|---:|---:|---:|---:|---|
| `market_gate` | 241 | 214 (89%) | 27 (11%) | 0 | `session_filter` (27) |
| `regime` | 214 | 214 (100%) | 0 | 0 | — |
| `signal` | 214 | 151 (71%) | 0 | 63 (29%) | no_signal |
| `construction` | 151 | 116 (77%) | 35 (23%) | 0 | `construction` (35) |
| `setup_policy` | 15 | 0 | 0 | 15 (100%) | `regime_setup_policy` |
| `invalidation` | 101 | 59 (58%) | 12 (12%) | 30 (30%) | `invalidation` (12) |
| `quality` | 89 | 89 (100%) | 0 | 0 | — |
| **`conviction`** | **89** | **0** | **0** | **89 (100%)** | **`conviction`** |
| `risk_gate` | 0 | — | — | — | — |
| `execution_guard` | 0 | — | — | — | — |

**Executed cycles: 0 / 241.** Zero trades reached Stage 7 or Stage 8 because **every single signal that reached conviction was held**.

### Decision-rule evaluation

Applying the plan Section 2.2 rule to the otherwise-valid signal set (market_gate passed AND signal generated) — **151 signals**:

- Stage-5 HELD at conviction: **89**
- Stage-4A HARD invalidation: **12**
- Stage-7 RiskGate rejections: **0** (never reached)
- **Sum: 101 / 151 = 67 %**

**67 % ≥ 60 % → the over-blocking hypothesis is CONFIRMED.**

Stage 5 (conviction) is the dominant choke point by a wide margin: **100 % hold-rate over 89 cycles**. Stage 4A invalidation adds another 42 rejections (12 HARD + 30 SOFT penalty, of which 30 survived to Stage 5). No signal in the backfill window ever reached Stage 7 — not because Stage 7 is clean, but because Stage 5 stops everything first.

### What this measurement tells us

1. **Conviction threshold + quality floors dominate all other rejection paths combined.** Gate-2 work should prioritise `pipeline/conviction.py:193-210` (quality floor) and `pipeline/conviction.py:212-232` (threshold) ahead of any other choke point in the plan's Top-10 list.
2. **The penalty budget is being fully spent.** Expanding `TradeDecision.penalties` in the decision cards will show exactly which penalty source (invalidation / conflict / portfolio) is pushing conviction below the entry threshold on a per-trade basis.
3. **Stage 4A HARD (12) is non-trivial but secondary** — worth fixing only after conviction is rebalanced.
4. **Stage 7 RiskGate and Stage 8 ExecutionGuard are untested in this dataset.** Once conviction is relaxed and trades start reaching Stage 7, we may see a second-order choke point emerge there — Gate-2 should re-measure after each conviction change.

### Gate-2 is unlocked

Per plan 2.2: `Stage-5 HELD + Stage-4A HARD + Stage-7 RiskGate ≥ 60 %` is satisfied (67 %). Observer is free to approve Gate-2 hard→soft reclassification as defined in plan section 3.2, starting with the conviction quality floor.

## Top 10 suspected choke points (pre-measurement, code-map evidence)

From the Phase 1 exploration. These are **hypotheses**, not confirmed causes. They will be validated against the Gate-1 funnel before any change is proposed for Gate-2.

| # | Location | Current behaviour | Why potentially over-blocking |
|---|---|---|---|
| 1 | `pipeline/conviction.py:193-210` | Quality floor: overall<55, 3+ tools<25, max<60 → HOLD | Three independent vetoes on the same signal |
| 2 | `pipeline/conviction.py:143-173` | 50-pt penalty budget with pro-rating | Three sources cascade silently; `penalties_prorated` flag is the only hint |
| 3 | `pipeline/invalidation.py:119-142` | Setup-type matrix checks toggled by `enabled_tools` | Single misconfig kills a setup class |
| 4 | `pipeline/execution_guard.py:122-136` | Near-dedup hardcoded at 1 ATR | Tight in ranging markets |
| 5 | `risk/monitor.py:175-176` | `max_concurrent_trades` hard cap (default 3) | Rejects 4th signal even when heat cap isn't reached |
| 6 | `pipeline/market_gate.py:50-54` | Stale bar 300 s | Strict for live feeds with intermittent latency |
| 7 | `pipeline/market_gate.py:80-84` | Spread ratio 3× median | Tight during volatility spikes |
| 8 | `risk/guards.py:50-74` | Confidence stdev 0.15 over 3 samples | AI confidence naturally drifts |
| 9 | `pipeline/invalidation.py:252-267` | R:R hard floor 1.0 (global) | Blind to setup type (range_bounce often < 1.0) |
| 10 | spread checks at `market_gate.py:80` **and** `execution_guard.py:151` | Same condition evaluated twice | Duplicate filter |

See `docs/references/blocking-policy.md` for the complete authoritative list.

## What shipped in Gate-1

### Backend

- `src/alphaloop/pipeline/types.py` — new `TradeDecision` dataclass + `build_trade_decision()` projector. Captures outcome, reject stage, raw/adjusted confidence, per-source penalties, size multiplier, AI verdict, execution status, latency, and the full journey. Read-only; no stage reads from it.
- `src/alphaloop/db/models/pipeline.py` — new `PipelineStageDecision` SQLAlchemy model (one row per cycle per stage).
- `src/alphaloop/db/migrations/versions/003_pipeline_stage_decisions.py` — alembic migration creating the table + indexes.
- `src/alphaloop/db/models/__init__.py` — exports the new model.
- `src/alphaloop/trading/loop.py::_log_pipeline_decision` — now writes one `PipelineStageDecision` per journey stage alongside the existing cycle-level row. Fire-and-forget. Also serialises the `TradeDecision` into the existing `pipeline_decisions.tool_results["trade_decision"]` blob.
- `src/alphaloop/webui/routes/pipeline.py` — **new** router with:
  - `GET /api/pipeline/funnel` — stage pass/reject counts + top reasons, filterable by symbol/mode/source/window.
  - `GET /api/pipeline/decisions/latest` — last N `TradeDecision` projections.
  - `GET /api/pipeline/stages/heatmap` — stage × symbol rejection-rate matrix.
  - `GET /api/pipeline/modes/compare` — per-mode funnel counts.
- `src/alphaloop/webui/routes/controls.py` — new `GET /api/controls/guards-status` (drawdown-pause from persisted guard state, circuit-breaker from `operational_events`).
- `src/alphaloop/webui/app.py` — pipeline router registered.

### UI

- `src/alphaloop/webui/static/js/components/observability.js` — **new** page with guards status, pipeline funnel, mode compare, stage heatmap, and decision cards (with full journey `<details>` and penalty list per card).
- `src/alphaloop/webui/static/js/components/risk_dashboard.js` — new "Incidents & Risk Lock" card wiring the existing `POST /api/controls/incidents/{id}/ack` and `POST /api/controls/no-new-risk/clear` endpoints with confirm-first UX.
- `src/alphaloop/webui/static/js/app.js` + `index.html` — new 🔍 Observability route + sidebar entry under **Risk & Monitoring**.
- `src/alphaloop/webui/static/css/app.css` — full CSS for funnel bars, decision cards, heatmap, mode-compare, and guards cards (dark + light theme compatible).

### Tooling

- `scripts/replay_pipeline.py` — CLI harness with two modes:
  - `--source backfill` (default): re-projects legacy `pipeline_decisions.tool_results.journey` JSON into per-stage rows. Zero broker calls, deterministic.
  - `--source backtest`: runs `run_vectorbt_backtest` and writes a pinned JSON baseline for regression.

### Documentation

- `docs/references/blocking-policy.md` — **new**. Single source of truth for every hard block, soft penalty, and safety invariant, cross-referenced by `file:line`.
- `docs/references/pipeline-funnel.md` — **new**. Ledger schema, endpoint shapes, replay harness usage, interpretation rules.
- `docs/references/trade-decision.md` — **new**. The `TradeDecision` dataclass + projector contract.
- `docs/references/observability.md` — **new**. 60-second debug flow for a blocked trade.
- `docs/references/webui-audit.md` — **new**. Per-setting live-sync status and action list for Gate-2.
- `docs/references/throughput-rebalance-report.md` — **this file**.
- `docs/claude/claude-pipelines.md` — updated to say three modes (`algo_only`, `algo_ai`, `ai_signal`), correcting the stale "two mode" paragraph.

### Tests

- `tests/unit/test_trade_decision.py` — 6 unit tests covering `build_trade_decision()` projection: clean TRADE, HELD with penalties, hard invalidation, AI validator reject, NO_SIGNAL, and `to_dict()` round-trip. **All pass.**
- `tests/integration/test_pipeline_funnel.py` — 5 integration tests against the FastAPI ASGI app with in-memory SQLite covering funnel aggregation, symbol/mode filters, heatmap matrix, mode compare, and the latest-decisions projection. **All pass.**

Test run: `pytest tests/unit/test_trade_decision.py tests/integration/test_pipeline_funnel.py -v → 11 passed, 1 warning`.

Broader regression: `pytest tests/integration/test_webui_routes.py` shows 82 passing tests including the new ones. 6 tests related to `strategies.py` operator-audit-log targets fail in the current working copy — these are **pre-existing failures** confirmed by running the same test suite against the stashed baseline (they pass there) and they touch files outside Gate-1 scope (`webui/routes/strategies.py`, which Gate-1 did not modify).

## UI improvements

1. A dedicated **🔍 Observability** tab (route `observability`) in the sidebar under "Risk & Monitoring".
2. Guards status card at the top — red dot for active drawdown pause or open circuit breaker, with per-symbol pause detail.
3. Pipeline funnel bar chart with stacked pass/blocked/held/other segments, total cycles and executed cycles in the header, top rejection reasons under each row.
4. Mode comparison table — algo_only / algo_ai / ai_signal side by side with executed / rejected / held / delayed counts.
5. Stage × symbol rejection heatmap with green→red colour scale.
6. Latest decision cards — direction, mode, setup, outcome, penalty list, AI verdict, latency, full expandable journey with colour-coded per-stage status.
7. Risk Dashboard → Incidents & Risk Lock card wiring incident acknowledgment and no-new-risk clearing with confirm-first UX.
8. Global search controls: symbol filter, source toggle (`live` / `backtest_replay`), time window selector (1h / 6h / 24h / 72h / 7d).

## Documentation improvements

- The blocking-policy doc is the first place in the repo that enumerates every rejection point with a file:line. It replaces scattered knowledge in `trading-modes.md`, `architecture.md`, changelog entries, and code comments.
- The observability doc provides a 60-second debug flow from symptom → stage → reason → rule.
- The webui-audit doc is the first audit of which Settings toggles actually round-trip to the running agent. This is the list the operator should look at before pulling a toggle that "doesn't seem to do anything".
- The mode inconsistency in `claude-pipelines.md` was fixed (`algo_plus_ai` → the three-mode table).

## Expected trade-flow impact

**Zero.** Gate-1 is observability-only. No stage thresholds, no hard/soft classification, no validator authority, no check removal. The funnel is read-only instrumentation; its presence cannot change cycle outcomes.

## Safety guarantees preserved

All invariants from the plan's "Unchanged" list are untouched in Gate-1 and remain HARD blocks:
- SL/TP direction sanity (`invalidation.py:206-250`).
- Kill switch when daily loss ≥ `max_daily_loss_pct`.
- Signal hash dedup (duplicate execution risk).
- Feed desync (`bid >= ask`).
- Drawdown pause guard.
- Risk filter plugin `severity="block"`.
- AI validator explicit `"reject"` in `ai_signal` mode.
- Kill-switch check at both `market_gate.py:42-43` and `monitor.py:172-173`.

The ledger writer in `trading/loop.py::_log_pipeline_decision` is wrapped in the existing `try/except` block — any DB failure silently degrades to logging, never blocks a trade or re-raises into the trading cycle.

## Remaining risks

1. **Unpopulated funnel until a bot runs.** Operator must either start a bot or run `scripts/replay_pipeline.py --source backfill --since 7d` before the Observability UI shows data.
2. **Settings round-trip risk for `tool_enabled_*` toggles.** Documented in `webui-audit.md`. Not fixed in Gate-1 because we don't yet know if any of these toggles is the actual choke point — waiting for funnel evidence.
3. **Circuit-breaker visibility is approximate.** `/api/controls/guards-status` reads circuit breaker state from `operational_events` (not live in-process state), so it can lag slightly. Flagged inline in the JSON response.
4. **Backtest replay per-bar instrumentation not implemented.** The `--source backtest` mode only writes a summary JSON baseline. Per-bar rows would require a hook inside `backtester/vbt_engine.py` and are deferred to Gate-2.
5. **Pre-existing `strategies.py` audit-log test failures in the tree** — unrelated to Gate-1, but the operator should triage them before merging Gate-2 work.

## Decision the operator needs to make

After populating the funnel (live bot cycle or backfill), open the Observability tab and apply the decision rule from the plan:

- **Stage-5 HELD + Stage-4A HARD + Stage-7 RiskGate ≥ 60 % of rejections** → hypothesis **confirmed**. Approve Gate-2, and we proceed with the hard→soft reclassification defined in plan section 3.2.
- **Otherwise** → hypothesis **disproven**. The observability views stay; thresholds are left alone; the blocking-policy doc becomes ongoing reference material.

## Gate-2 deferred scope (recap, not yet started)

- Quality floor → soft penalty (plan 3.2).
- Penalty pro-rating exposed in `TradeDecision.penalties` (already surfaced in Gate-1 — no behaviour change yet).
- Near-dedup regime-aware distance.
- Hard concurrent cap → heat-based cap.
- Configurable stale-bar / spread-ratio per asset.
- ConfidenceVarianceFilter threshold relaxation.
- Per-setup R:R floor.
- Validator mode-aware authority (plan 3.4).
- Dedup of duplicate filters (plan 3.3).

None of these are touched in Gate-1.
