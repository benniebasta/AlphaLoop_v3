"""Phase 2H: One-time backfill for OPEN trades with null order_ticket.

Attempts to match DB trades to broker positions by symbol, time, volume,
and fill price. Unresolvable rows are marked needs_manual_review.

Usage:
    python -m alphaloop.db.backfill_tickets [--dry-run] [--symbol XAUUSD]
"""

import asyncio
import argparse
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def backfill_tickets(
    session_factory,
    executor=None,
    *,
    symbol: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Attempt to match OPEN trades with null tickets to broker positions.

    Returns a summary dict with counts.
    """
    from sqlalchemy import select, update
    from alphaloop.db.models.trade import TradeLog

    summary = {
        "total_open_null_ticket": 0,
        "matched": 0,
        "unresolvable": 0,
        "skipped": 0,
    }

    async with session_factory() as session:
        # Find OPEN trades with no order_ticket
        q = select(TradeLog).where(
            TradeLog.outcome == "OPEN",
            TradeLog.order_ticket.is_(None),
        )
        if symbol:
            q = q.where(TradeLog.symbol == symbol)

        result = await session.execute(q)
        null_ticket_trades = list(result.scalars())
        summary["total_open_null_ticket"] = len(null_ticket_trades)

        if not null_ticket_trades:
            logger.info("[backfill] No OPEN trades with null order_ticket found")
            return summary

        logger.info(
            "[backfill] Found %d OPEN trades with null order_ticket",
            len(null_ticket_trades),
        )

        # Get broker positions for matching
        broker_positions = []
        if executor:
            try:
                broker_positions = await executor.get_open_positions()
            except Exception as e:
                logger.error("[backfill] Failed to get broker positions: %s", e)

        # Build broker lookup by (symbol, direction, volume)
        broker_by_key: dict[tuple, list] = {}
        for pos in broker_positions:
            key = (
                getattr(pos, "symbol", ""),
                getattr(pos, "direction", "").upper(),
                round(getattr(pos, "volume", 0), 2),
            )
            broker_by_key.setdefault(key, []).append(pos)

        for trade in null_ticket_trades:
            key = (trade.symbol or "", (trade.direction or "").upper(), round(trade.lot_size or 0, 2))
            candidates = broker_by_key.get(key, [])

            matched = False
            for pos in candidates:
                # Match by price proximity (within 1% of entry)
                pos_entry = getattr(pos, "entry_price", 0)
                trade_entry = trade.entry_price or 0
                if trade_entry > 0 and pos_entry > 0:
                    diff_pct = abs(pos_entry - trade_entry) / trade_entry
                    if diff_pct < 0.01:  # within 1%
                        ticket = getattr(pos, "ticket", None)
                        if ticket:
                            if dry_run:
                                logger.info(
                                    "[backfill] DRY-RUN: Would set trade %d ticket=%d "
                                    "(matched by %s %.2f lots @ %.2f)",
                                    trade.id, ticket, key[0], key[2], pos_entry,
                                )
                            else:
                                trade.order_ticket = ticket
                                logger.info(
                                    "[backfill] Set trade %d ticket=%d",
                                    trade.id, ticket,
                                )
                            # Remove from candidates to avoid double-matching
                            candidates.remove(pos)
                            matched = True
                            summary["matched"] += 1
                            break

            if not matched:
                summary["unresolvable"] += 1
                if not dry_run:
                    # Mark for manual review via post_trade_notes
                    trade.post_trade_notes = (
                        f"{trade.post_trade_notes or ''}\n"
                        f"[BACKFILL] needs_manual_review — no broker match found "
                        f"({datetime.now(timezone.utc).isoformat()})"
                    ).strip()
                logger.warning(
                    "[backfill] UNRESOLVABLE: trade %d (%s %s %.2f lots @ %.2f) — "
                    "no broker position match",
                    trade.id, trade.symbol, trade.direction,
                    trade.lot_size or 0, trade.entry_price or 0,
                )

        if not dry_run:
            await session.commit()

    logger.info(
        "[backfill] Complete: %d total, %d matched, %d unresolvable",
        summary["total_open_null_ticket"],
        summary["matched"],
        summary["unresolvable"],
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description="Backfill null order_tickets on OPEN trades")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview only (default)")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes")
    parser.add_argument("--symbol", default=None, help="Filter by symbol")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def _run():
        from alphaloop.core.config import AppConfig
        from alphaloop.app import create_app

        config = AppConfig()
        container = await create_app(config, symbol=args.symbol or "XAUUSD", instance_id="backfill", dry_run=True)

        # Optionally connect executor for broker matching
        from alphaloop.execution.mt5_executor import MT5Executor
        executor = MT5Executor(symbol=args.symbol or "XAUUSD", dry_run=True)
        try:
            await executor.connect()
        except Exception:
            executor = None

        result = await backfill_tickets(
            container.db_session_factory,
            executor=executor,
            symbol=args.symbol,
            dry_run=not args.apply,
        )
        print(f"Result: {result}")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
