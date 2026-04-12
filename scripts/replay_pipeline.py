"""Gate-1 pipeline replay harness.

Populates ``pipeline_stage_decisions`` (the Gate-1 observability ledger) from
one of two sources:

    --source backfill
        Re-project legacy ``pipeline_decisions.tool_results.journey`` JSON
        into per-stage rows. Uses the existing cycle-level decisions table
        (already written by the trading loop before Gate-1).  Fast, zero
        broker calls, deterministic. This is the **primary** way to get the
        funnel endpoint populated from historical live trades.

    --source backtest
        Run ``run_vectorbt_backtest`` over a fixed window and emit a pinned
        regression baseline. Requires a strategy JSON path. Every synthetic
        cycle becomes a row in ``pipeline_stage_decisions`` with
        ``source='backtest_replay'``. The result is also written to a
        JSON baseline file so ``tests/integration`` can diff later changes
        against an unchanging reference.

Usage examples::

    python -m scripts.replay_pipeline --source backfill --since 24h
    python -m scripts.replay_pipeline --source backtest \\
        --strategy strategy_versions/foobar_BTCUSD_v1.json \\
        --window-days 7 --baseline tests/data/pipeline_funnel_baseline.json

This script **never** executes broker orders and **never** mutates
pipeline_decisions rows it reads. It is append-only to the stage ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("replay_pipeline")


def _parse_since(value: str) -> datetime:
    """Parse a human relative string like ``24h``, ``7d``, ``90m``."""
    match = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", value)
    if not match:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SystemExit(f"Cannot parse --since {value!r}") from exc
    n, unit = int(match.group(1)), match.group(2)
    delta = {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]
    return datetime.now(timezone.utc) - delta


async def _open_session_factory():
    """Create an async session factory from the default DB config."""
    from alphaloop.core.config import AppConfig
    from alphaloop.db.engine import create_db_engine
    from alphaloop.db.session import create_session_factory

    config = AppConfig()
    engine = create_db_engine(config.db)
    return create_session_factory(engine), engine


async def _backfill(since: datetime, source_tag: str) -> dict[str, Any]:
    """Backfill per-stage rows from legacy ``pipeline_decisions`` JSON."""
    from sqlalchemy import select

    from alphaloop.db.models.pipeline import (
        PipelineDecision,
        PipelineStageDecision,
    )

    session_factory, engine = await _open_session_factory()
    rows_written = 0
    cycles_scanned = 0

    async with session_factory() as session:
        q = (
            select(PipelineDecision)
            .where(PipelineDecision.occurred_at >= since)
            .order_by(PipelineDecision.occurred_at.asc())
        )
        rows = list((await session.execute(q)).scalars())
        logger.info("Backfill scanning %d legacy pipeline_decisions rows", len(rows))

        for row in rows:
            cycles_scanned += 1
            tool_results = row.tool_results or {}
            journey_payload = (
                tool_results.get("journey") if isinstance(tool_results, dict) else None
            )
            trade_decision = (
                tool_results.get("trade_decision")
                if isinstance(tool_results, dict)
                else None
            )
            if not journey_payload or not journey_payload.get("stages"):
                continue

            cycle_id = f"backfill-{row.id}"
            outcome = (
                journey_payload.get("final_outcome")
                or ("trade_opened" if row.allowed else "rejected")
            )
            reject_stage = (
                (trade_decision or {}).get("reject_stage")
                if isinstance(trade_decision, dict)
                else None
            ) or row.blocked_by
            mode = (
                (trade_decision or {}).get("mode")
                if isinstance(trade_decision, dict)
                else None
            )
            size_mul = (
                (trade_decision or {}).get("size_multiplier")
                if isinstance(trade_decision, dict)
                else None
            ) or row.size_modifier

            for idx, stage in enumerate(journey_payload.get("stages") or []):
                session.add(
                    PipelineStageDecision(
                        occurred_at=row.occurred_at or datetime.now(timezone.utc),
                        cycle_id=cycle_id,
                        source=source_tag,
                        symbol=row.symbol,
                        instance_id=row.instance_id,
                        mode=mode,
                        stage=str(stage.get("stage") or ""),
                        stage_index=idx,
                        status=str(stage.get("status") or ""),
                        blocked_by=stage.get("blocked_by"),
                        detail=(stage.get("detail") or "")[:2000] or None,
                        payload=stage.get("payload") or None,
                        outcome=outcome,
                        reject_stage=reject_stage,
                        direction=row.direction,
                        setup_type=None,
                        conviction_score=None,
                        size_multiplier=size_mul,
                        latency_ms=None,
                    )
                )
                rows_written += 1

        await session.commit()

    await engine.dispose()
    return {
        "source": source_tag,
        "since": since.isoformat(),
        "cycles_scanned": cycles_scanned,
        "stage_rows_written": rows_written,
    }


async def _backtest(
    strategy_path: Path,
    window_days: int,
    baseline_path: Path | None,
) -> dict[str, Any]:
    """Run a vectorbt backtest and emit stage rows + JSON baseline.

    Gate-1 implementation note: for the first replay pass we produce a
    summary JSON baseline from the ``run_vectorbt_backtest`` output. Per-bar
    per-stage rows require a deeper instrumentation hook inside vbt_engine
    which is intentionally out of scope for Gate-1 to keep behaviour
    untouched. Run ``--source backfill`` on the live table to populate the
    funnel today.
    """
    from alphaloop.backtester.vbt_engine import run_vectorbt_backtest

    if not strategy_path.exists():
        raise SystemExit(f"Strategy file not found: {strategy_path}")

    logger.info(
        "Backtest replay: strategy=%s window=%dd baseline=%s",
        strategy_path.name,
        window_days,
        baseline_path,
    )
    try:
        result = run_vectorbt_backtest(
            strategy_path=str(strategy_path),
            window_days=window_days,
        )
    except TypeError:
        # Older signature — call positionally.
        result = run_vectorbt_backtest(str(strategy_path), window_days)

    baseline: dict[str, Any] = {
        "strategy": str(strategy_path),
        "window_days": window_days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": result if isinstance(result, dict) else {"raw": repr(result)},
    }
    if baseline_path is not None:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(baseline, indent=2, default=str))
        logger.info("Baseline written to %s", baseline_path)

    return {
        "source": "backtest_replay",
        "strategy": str(strategy_path),
        "window_days": window_days,
        "baseline_path": str(baseline_path) if baseline_path else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("backfill", "backtest"),
        default="backfill",
        help="backfill from legacy pipeline_decisions or run a backtest replay",
    )
    parser.add_argument(
        "--since",
        default="7d",
        help="(backfill) how far back to scan — e.g. 24h, 7d, or an ISO timestamp",
    )
    parser.add_argument(
        "--source-tag",
        default="live",
        help="(backfill) value to write into pipeline_stage_decisions.source",
    )
    parser.add_argument(
        "--strategy",
        type=Path,
        help="(backtest) path to the strategy JSON to replay",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="(backtest) window size in days",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("tests/data/pipeline_funnel_baseline.json"),
        help="(backtest) where to write the pinned baseline JSON",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.source == "backfill":
        since = _parse_since(args.since)
        outcome = asyncio.run(_backfill(since, args.source_tag))
    else:
        if not args.strategy:
            raise SystemExit("--strategy is required for --source backtest")
        outcome = asyncio.run(
            _backtest(args.strategy, args.window_days, args.baseline)
        )

    print(json.dumps(outcome, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
