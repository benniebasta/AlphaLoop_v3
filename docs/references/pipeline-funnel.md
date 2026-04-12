# Pipeline funnel — instrumentation, endpoint, replay harness

**Purpose.** Measure where trades die in the 8-stage pipeline so the Gate-2 throughput-rebalance decision can be evidence-based. No behaviour changes.

## The ledger

Every pipeline cycle writes one row per stage into `pipeline_stage_decisions` (`db/models/pipeline.py::PipelineStageDecision`), alongside the existing cycle-level `pipeline_decisions` row.

Schema (see migration `003_pipeline_stage_decisions.py`):

| Column | Type | Notes |
|---|---|---|
| `id` | PK | autoincrement |
| `occurred_at` | datetime | cycle timestamp |
| `cycle_id` | string | `{instance_id}-{epoch_ms}`; stable per cycle |
| `source` | string | `live` \| `backtest_replay` |
| `symbol` | string | trading symbol |
| `instance_id` | string | loop instance id |
| `mode` | string | `algo_only` \| `algo_ai` \| `ai_signal` |
| `stage` | string | `market_gate`, `regime`, `signal`, `construction`, `invalidation`, `quality`, `conviction`, `ai_validator`, `risk_gate`, `execution_guard`, `sizing`, … |
| `stage_index` | int | position in the journey |
| `status` | string | `passed`, `blocked`, `held`, `hard_invalidated`, `soft_invalidated`, `no_signal`, `no_construction`, `delay`, `execute`, … |
| `blocked_by` | string | stage name that blocked (if any) |
| `detail` | text | human-readable reason |
| `payload` | JSON | per-stage metrics (e.g. `data_quality`, `spread_ratio`, `conviction_score`) |
| `outcome` | string | `CycleOutcome.value` duplicated for group-by |
| `reject_stage` | string | stage that produced the final cycle rejection |
| `direction`, `setup_type` | string | from the candidate signal |
| `conviction_score` | float | stage-5 score |
| `size_multiplier` | float | product of all sizing scalars |
| `latency_ms` | float | cycle elapsed ms |

Write point: `trading/loop.py::TradingLoop._log_pipeline_decision` (see the `# Per-stage funnel ledger` block). Behaviour is fire-and-forget; write failures never affect trading.

## The endpoint

`GET /api/pipeline/funnel`

Query params:
- `symbol` — optional filter.
- `source` — default `live`; pass `backtest_replay` to read the backtest baseline.
- `mode` — `algo_only` \| `algo_ai` \| `ai_signal`.
- `hours` — window size (default 24, max 720).
- `since` — ISO timestamp, overrides `hours`.

Response shape:

```json
{
  "window_start": "...",
  "total_cycles": 1234,
  "executed_cycles": 17,
  "stages": [
    {
      "stage": "market_gate",
      "total": 1234,
      "passed": 1200,
      "blocked": 34,
      "held": 0,
      "other": 0,
      "top_reasons": [{"reason": "abnormal_spread", "count": 22}]
    },
    ...
  ]
}
```

Stages are returned in canonical order (see `_CANONICAL_STAGES` in `webui/routes/pipeline.py`). Unknown stages are appended after — no code change needed when new stages are added.

Related endpoints on the same router:
- `GET /api/pipeline/decisions/latest` — last N `TradeDecision` projections.
- `GET /api/pipeline/stages/heatmap` — stage × symbol rejection rate.
- `GET /api/pipeline/modes/compare` — per-mode funnel for algo_only / algo_ai / ai_signal.

## The replay harness

`scripts/replay_pipeline.py` populates `pipeline_stage_decisions` without touching the running agent.

### Backfill from legacy data (recommended first step)

```bash
python -m scripts.replay_pipeline --source backfill --since 7d
```

This reads the last 7 days of `pipeline_decisions` rows (the existing cycle-level table that the loop has been writing since before Gate-1), re-projects the `tool_results.journey` JSON into per-stage rows, and writes them to `pipeline_stage_decisions` with `source="live"`. Zero broker calls, deterministic, append-only.

Flags:
- `--since 24h` / `--since 7d` / `--since 2026-04-05T00:00:00Z` — how far back to scan.
- `--source-tag backfill` — override the `source` column if you want to segregate the backfill from a running live stream.

### Backtest replay (pinned regression baseline)

```bash
python -m scripts.replay_pipeline --source backtest \
  --strategy strategy_versions/your_strategy.json \
  --window-days 7 \
  --baseline tests/data/pipeline_funnel_baseline.json
```

This runs `run_vectorbt_backtest` over a fixed window and writes a JSON baseline. Later Gate-2 changes diff against this baseline so any behaviour drift is visible.

Note: the per-bar per-stage emission inside the vbt engine is out of scope for Gate-1 (it would require a new hook inside `backtester/vbt_engine.py`). For Gate-1 we use the JSON summary only; see `tests/integration/test_pipeline_funnel.py` for how the baseline is consumed.

## Interpreting the funnel

The **decision rule** from the plan (Phase 2.2): if `Stage-5 HELD + Stage-4A HARD + Stage-7 RiskGate` account for **≥60%** of rejections of otherwise-valid signals (i.e. cycles that passed Stage 1–3), the over-blocking hypothesis is considered **confirmed** and Gate-2 is unlocked.

When reading the funnel:
- Focus on `blocked + held` ÷ `total` per stage.
- Cross-reference with `top_reasons` — a single dominant reason usually points to a miscalibration (e.g. `quality_floor` at Stage 5 ≫ all other reasons).
- Compare `executed_cycles` across modes via `/api/pipeline/modes/compare`. Large gaps are a sign that one mode's validator authority is too aggressive.

Any stage where the funnel shows more than **40%** rejection over a 7-day window is a candidate for the Gate-2 hard→soft reclassification.
