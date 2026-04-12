# TradeDecision — the per-cycle explainability object

**Location:** `src/alphaloop/pipeline/types.py`

`TradeDecision` is a read-only projection of a `PipelineResult` into a single
dataclass that contains everything the UI needs to explain a cycle. It never
drives behaviour and is never mutated by downstream stages — it is built once
in `build_trade_decision()` after the orchestrator finalises the cycle, and
then persisted into `pipeline_decisions.tool_results["trade_decision"]` by
`trading/loop.py::_log_pipeline_decision`.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `symbol` | string | trading symbol |
| `mode` | string | `algo_only` \| `algo_ai` \| `ai_signal` |
| `direction` | `BUY` \| `SELL` \| None | direction of the candidate (if any) |
| `setup_type` | string | `pullback`, `breakout`, `reversal`, … |
| `outcome` | string | `CycleOutcome.value` — `no_signal`, `rejected`, `held`, `delayed`, `trade_opened`, `order_failed` |
| `reject_stage` | string | name of the stage that produced the rejection (or the last journey stage for no_signal) |
| `reject_reason` | string | human text — often the invalidation failure or the conviction hold reason |
| `confidence_raw` | float | the raw signal confidence coming out of the signal engine |
| `confidence_adjusted` | float | `conviction.normalized` after penalties were applied |
| `conviction_score` | float | stage-5 conviction score (0–100) |
| `conviction_decision` | string | `TRADE` \| `HOLD` |
| `penalties` | list of `{source, points, reason}` | every penalty that was applied at stage 5 (invalidation, conflict, portfolio, budget_cap, quality_floor) |
| `size_multiplier` | float | **product** of all sizing scalars (`conviction_scalar × regime_scalar × freshness_scalar × risk_gate_scalar × equity_curve_scalar`). Zero when the cycle did not reach sizing. |
| `hard_block` | bool | True when `reject_stage` is a hard-safety stage (market gate, invalidation, risk gate, execution guard BLOCK, AI validator reject) |
| `ai_verdict` | string | `approve` \| `reduce` \| `reject` \| `skipped` — derived from the `ai_validator` journey entry |
| `execution_status` | string | `executed` \| `delayed` \| `blocked` \| `none` |
| `latency_ms` | float | `PipelineResult.elapsed_ms` |
| `journey` | `CandidateJourney` | full per-stage trail (stages, statuses, details, blocked_by, payload) |
| `occurred_at` | datetime | build time |

## How the UI uses it

1. `/api/pipeline/decisions/latest` returns the projected JSON for the last N cycles.
2. The observability component renders one card per decision (see `webui/static/js/components/observability.js::renderDecisionCard`) showing direction, mode, conviction, penalty list, AI verdict, latency, and the full journey under a `<details>`.
3. Decision rejection stage and reason are colour-coded: green for `trade_opened`, red for `rejected` / `order_failed`, amber for `held` / `no_signal` / `no_construction`, blue for `delayed`.

## How to extend

If a new stage is added to the orchestrator, update `build_trade_decision()` so the new stage's outputs (penalties, reasons, metrics) flow through. The function is intentionally defensive — missing attributes on `result` just leave fields `None` rather than erroring.

## Why a projection, not a new source of truth

`TradeDecision` is deliberately decoupled from the orchestrator: it reads from `PipelineResult` after `_finalise()` and never feeds back. This means Gate-1 can add the object without any risk of a behaviour regression — no stage reads from it, nothing routes on its fields.
