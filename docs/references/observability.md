# Observability — how to debug a blocked trade in under 60 seconds

Gate-1 ships four read-only views that together let the operator answer
"why did that trade die?" without `grep`-ing logs. This file is the user
guide.

## The four views (all on the Observability tab)

1. **Guards status** — top of the page. Red dot = drawdown pause or circuit
   breaker active. Reads from `/api/controls/guards-status`, which reads the
   persisted guard state from `app_settings["risk_guard_state"]` and the last
   `operational_event` rows tagged `circuit_breaker`.

2. **Pipeline funnel** — bar chart of pass / blocked / held / delay per stage
   for the selected window. `/api/pipeline/funnel`. If one stage has >40%
   rejection, it is the primary choke point.

3. **Mode comparison** — per-mode (`algo_only` / `algo_ai` / `ai_signal`)
   counts of cycles, executed, rejected, held, delayed. `/api/pipeline/modes/compare`.
   Large gaps point to mode-specific validator over-authority.

4. **Stage × symbol heatmap** — `/api/pipeline/stages/heatmap`. Rejection
   rate per `(stage, symbol)` tile. A hot row means the stage is calibrated
   wrong for that asset.

5. **Latest trade decisions** — cards, one per cycle, from
   `/api/pipeline/decisions/latest`. Each card shows direction, mode, raw
   and adjusted confidence, conviction, size multiplier, AI verdict, latency,
   every penalty applied, and the full journey under a `<details>`.

## Debug flow

1. Open **Observability**.
2. If the Guards card is red → the loop is globally paused. Go to **Risk**
   and check / clear the active incident(s).
3. Check the **funnel**. The stage with the highest `blocked + held` ÷
   `total` is the choke point. Note its top reasons.
4. Scroll to **Latest trade decisions** and expand the journey on a card
   whose `reject_stage` matches that stage. The journey's `detail` and
   `payload` columns contain the exact metric that tripped the block.
5. Cross-reference against `docs/references/blocking-policy.md` to see
   whether the rule is intentional or a miscalibration.

## Populating the funnel for the first time

Fresh installs will have an empty funnel until the bot runs. You can backfill
from the legacy `pipeline_decisions` table (which has been writing journey
JSON for a while):

```bash
python -m scripts.replay_pipeline --source backfill --since 7d
```

This writes per-stage rows into `pipeline_stage_decisions` without touching
the running agent.

## What to screenshot for the Gate-2 review

When you write the throughput-rebalance report you want, capture:
- Funnel for the last 7 days, filtered by each symbol.
- Mode-compare for the same window.
- Two or three decision cards where `reject_stage` is `conviction` or
  `invalidation` with a fully-expanded journey.

These three artefacts are enough to prove or disprove the over-blocking
hypothesis defined in `imperative-splashing-gizmo.md` Phase 2.2.

## Cross-references

- `blocking-policy.md` — authoritative list of every hard block and soft penalty.
- `pipeline-funnel.md` — ledger schema, endpoint shape, replay harness.
- `trade-decision.md` — the `TradeDecision` dataclass.
- `webui-audit.md` — which settings toggles are live-synced to running agents.
