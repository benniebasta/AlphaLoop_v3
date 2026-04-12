# Blocking policy — hard blocks, soft penalties, size reductions

**Status:** Gate-1 observability documentation. This file describes the **current** behaviour of the 8-stage pipeline as of 2026-04-12. No thresholds are changed in Gate-1 — this is the single source of truth against which any future Gate-2 reclassification will be compared.

Every row below cites a `file:line` location. If you change the code, update this table in the same commit.

## Legend

| Kind | Meaning |
|---|---|
| **HARD** | Trade is killed. `CycleOutcome.REJECTED`. |
| **HELD** | Trade is held; no execution. `CycleOutcome.HELD` or `NO_SIGNAL` / `NO_CONSTRUCTION`. |
| **DELAY** | Trade is queued for re-evaluation on a later cycle. `CycleOutcome.DELAYED`. |
| **SOFT** | Conviction penalty or size multiplier reduction. Cycle continues. |
| **LOG** | Informational only — no behaviour change. |

## Stage 1 — MarketGate (`pipeline/market_gate.py`)

| File:line | Check | Kind | Notes |
|---|---|---|---|
| `market_gate.py:42-43` | Kill switch active | HARD | Reads from `RiskMonitor._kill_switch`. |
| `market_gate.py:50-54` | Stale bar age > `stale_bar_seconds` (default 300) | HARD | Per-symbol override not yet wired. |
| `market_gate.py:59-63` | Bars available < `min_bars_required` (default 200) | HARD | |
| `market_gate.py:71-72` | Feed desync (`bid >= ask`) | HARD | Invariant: never cross book. |
| `market_gate.py:74-75` | Negative spread | HARD | |
| `market_gate.py:80-84` | Spread > `spread_ratio_max × median` (default 3.0) | HARD | |
| `market_gate.py:93-107` | Tool plugin at gate stage | HARD or SOFT | Severity per tool: `block` or `reduce`. |

## Stage 2 — RegimeClassifier (`pipeline/regime.py`)

Never blocks. Emits `RegimeSnapshot` which parameterises downstream stages via:
- `regime.confidence_ceiling` — caps conviction output (SOFT).
- `regime.size_multiplier` — final sizing scalar (SOFT).
- `regime.min_entry_adjustment` — raises conviction threshold at Stage 5 (SOFT).
- `regime.allowed_setups` — if non-empty and setup not allowed, Stage 3B→5 HELD via `setup_policy` journey entry (see `orchestrator.py:361-381`).

## Stage 3 — Signal Generation (`signals/engine.py`, `signals/algorithmic.py`)

| Location | Check | Kind |
|---|---|---|
| `orchestrator.py:213-221` | Signal generator returns `None` | HELD (`NO_SIGNAL`) |
| `signals/algorithmic.py:98-119` | Agreement logic AND/OR/MAJORITY produces no agreement | HELD (via `None`) |

## Stage 3B — TradeConstruction (`pipeline/construction.py`)

| Location | Check | Kind |
|---|---|---|
| `orchestrator.py:271-292` | `construction.signal is None` | HELD (`NO_CONSTRUCTION`) |
| `construction.py` | No valid SL candidate (swing→FVG→ATR) | HELD |

## Stage 4A — StructuralInvalidation (`pipeline/invalidation.py`)

Universal checks (every setup):

| File:line | Check | Kind |
|---|---|---|
| `invalidation.py:206-230` | SL on wrong side of entry | HARD |
| `invalidation.py:232-250` | TP on wrong side of entry | HARD |
| `invalidation.py:252-267` | `rr_ratio < rr_hard_min` (default 1.0) | HARD |
| `invalidation.py:252-267` | `rr_ratio < rr_soft_min` (default 1.5) | SOFT (penalty on conviction) |
| `invalidation.py:269-287` | SL distance out of `[sl_min_points, sl_max_points]` | HARD |
| `invalidation.py:291-306` | `raw_confidence < confidence_hard_min` (default 0.30) | HARD |
| `invalidation.py:310-333` | Setup not in `regime.allowed_setups` | HARD (converted to HELD by orchestrator in `setup_policy` branch) |
| `invalidation.py:119-142` | Setup-type matrix (`bos_required`, `ema200_alignment`, `swing_alignment`, `exhaustion_required`, `swing_ranging`, `bollinger_position`) | HARD/SOFT depending on `enabled_tools[*]` severity |
| `invalidation.py:145-166` | Plugin tools with `severity="block"` | HARD |
| `invalidation.py:145-166` | Plugin tools with `severity="warn"` | SOFT |

## Stage 4B — StructuralQuality (`pipeline/quality.py`)

Never blocks. Produces:
- `quality.overall_score` — 0–100 feeds into Stage 5 quality floor.
- `quality.group_scores` — per-group scores (momentum, structure, regime, execution, risk).
- `quality.tool_scores` — per-tool 0–100 scores.
- `quality.low_score_count` — count of tools below `_QUALITY_FLOOR_CONTRADICTION_THRESHOLD` (25).
- `quality.max_score` — the highest tool score observed.

## Stage 5 — ConvictionScorer (`pipeline/conviction.py`)

| File:line | Check | Kind |
|---|---|---|
| `conviction.py:44-65` | `overall_score < _QUALITY_FLOOR_OVERALL` (55.0) | **HELD** (quality floor) |
| `conviction.py:44-65` | `low_score_count >= _QUALITY_FLOOR_CONTRADICTION_COUNT` (3) | **HELD** (quality floor) |
| `conviction.py:44-65` | `max_score < _QUALITY_FLOOR_MAX_SCORE_MIN` (60.0) | **HELD** (quality floor) |
| `conviction.py:143-173` | Invalidation penalty (from Stage 4A SOFT) | SOFT (up to penalty budget) |
| `conviction.py:143-173` | Conflict penalty (group score spread > 40) | SOFT (max 30.0) |
| `conviction.py:143-173` | Portfolio penalty (macro + risk budget low) | SOFT (max 25.0) |
| `conviction.py:143-173` | Penalty budget cap `MAX_TOTAL_CONVICTION_PENALTY` (50.0) | Pro-rated across sources if exceeded |
| `conviction.py:212-232` | `adjusted_conviction >= 75.0` | TRADE size 1.0× |
| `conviction.py:212-232` | `adjusted_conviction >= 60 + regime.min_entry_adjustment` | TRADE size 0.6× |
| `conviction.py:212-232` | Below both thresholds | **HELD** (insufficient edge) |

## Stage 6 — BoundedAIValidator (`pipeline/ai_validator.py`)

Runs only in `algo_ai` and `ai_signal` modes.

| File:line | Check | Kind |
|---|---|---|
| `ai_validator.py:129-135` | AI returns `status in ("rejected", "reject")` | HARD |
| `ai_validator.py:111-114` | AI call error with `fail_open=False` (live mode default) | HARD |
| `ai_validator.py:111-114` | AI call error with `fail_open=True` | LOG (auto-approve with warning) |

Invariants: AI validator cannot change direction, setup type, SL, TP, or raise confidence by more than +0.05.

## Stage 7 — RiskGate (`pipeline/risk_gate.py` + `risk/monitor.py` + `risk/guards.py`)

| File:line | Check | Kind |
|---|---|---|
| `risk_gate.py:63-75` | Risk-filter plugin `severity="block"` | HARD |
| `risk_gate.py:78-86` | `RiskMonitor.can_open_trade()` returns false | HARD |
| `monitor.py:163` | Not seeded from DB | HARD |
| `monitor.py:168-170` | `no_new_risk` active | HARD |
| `monitor.py:172-173` | Kill switch active | HARD |
| `monitor.py:175-176` | `open_trades >= max_concurrent_trades` (default 3) | HARD |
| `monitor.py:179-181` | `open_risk_usd >= heat_cap` | HARD |
| `monitor.py:183-184` | Account balance ≤ 0 | HARD |
| `monitor.py:186-189` | `daily_loss_pct >= max_daily_loss_pct` | HARD + activates kill switch |
| `monitor.py:192-197` | Session loss cap exceeded | HARD |
| `monitor.py:211-212` | Trade frequency cap (`max_trades_per_hour`) | HARD |
| `risk_gate.py:90-96` | `DrawdownPauseGuard.is_paused(symbol)` | HARD |
| `risk_gate.py:99-110` | `PortfolioCapGuard.is_capped()` (default `portfolio_cap_pct=0.06`) | HARD |
| `risk_gate.py:112-133` | `CorrelationGuard` `severity="block"` | HARD |
| `risk_gate.py:136-142` | `EquityCurveScaler.scale()` (deviation-based 0.25–1.0) | SOFT (size) |

## Stage 8 — ExecutionGuard (`pipeline/execution_guard.py` + `risk/guards.py`)

| File:line | Check | Kind |
|---|---|---|
| `execution_guard.py:111-119` | Duplicate signal hash in last N cycles | HARD (BLOCK) |
| `execution_guard.py:122-136` | Open position within 1 ATR (hardcoded) | HARD (BLOCK) |
| `execution_guard.py:139-146` | Confidence stdev > 0.15 over last 3 samples | HARD (BLOCK) |
| `execution_guard.py:151-160` | Spread > `median × threshold` (default 1.8) | DELAY (up to 3 candles) |
| `execution_guard.py:163-185` | Tick-jump > 0.8 ATR | DELAY (1 candle) |
| `execution_guard.py:188-214` | Liquidity vacuum (extreme body ratio, no volume) | DELAY (1 candle) |

## Post-pipeline — Freshness & sizing

| File:line | Check | Kind |
|---|---|---|
| `orchestrator.py:579-589` | `freshness <= 0` | HELD |
| `orchestrator.py:617-633` | `shadow_mode=True` on the orchestrator | HELD (simulation only) |

## Duplicate filtering — currently present

These checks evaluate the same market condition at two different stages. They are documented here but **not changed in Gate-1**. Gate-2 will propose removal.

1. **Spread sanity** — `market_gate.py:80-84` (HARD) + `execution_guard.py:151-160` (DELAY).
2. **Confidence floor** — `invalidation.py:291-306` (HARD) + `conviction.py:212-232` quality-floor-implied minimum.
3. **Kill switch** — `market_gate.py:42-43` (HARD) + `monitor.py:172-173` inside `risk_gate` (HARD).

## Safety invariants — never to be removed

Regardless of any future Gate-2 reclassification, these remain **HARD**:
- SL/TP direction sanity (`invalidation.py:206-250`).
- Kill switch when daily loss ≥ `max_daily_loss_pct`.
- Signal hash dedup (duplicate execution risk) at `execution_guard.py:111-119`.
- Feed desync `bid >= ask` at `market_gate.py:71-72`.
- Drawdown pause guard at `risk_gate.py:90-96`.
- Risk filter plugin `severity="block"` at `risk_gate.py:63-75`.
- AI validator explicit `"reject"` in `ai_signal` mode (`ai_validator.py:129-135`).

## How to read the funnel vs this table

The funnel endpoint (`GET /api/pipeline/funnel`) buckets journey rows into `passed` / `blocked` / `held` / `other`:
- `blocked` ↔ HARD rows above.
- `held` ↔ HELD rows above (quality floors, conviction thresholds, `NO_SIGNAL`, `NO_CONSTRUCTION`, `soft_invalidated`).
- `other` ↔ DELAY rows.
- `passed` ↔ stage transition accepted (no blocker).

Any stage where `blocked + held > 40%` of `total` over a meaningful window is a candidate for the Gate-2 throughput-rebalance review.
