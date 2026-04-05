"""
Position reconciliation engine.

On startup or after a crash, syncs broker-side positions with the
local DB state. Detects orphaned positions, missing records, and
price/SL/TP discrepancies.

Usage:
    reconciler = PositionReconciler(executor, trade_repo)
    report = await reconciler.reconcile()
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ReconciliationIssue:
    """A single discrepancy found during reconciliation."""

    ticket: int
    symbol: str
    issue_type: str  # orphaned_broker, orphaned_db, sl_mismatch, tp_mismatch, volume_mismatch
    description: str
    severity: str = "warning"  # warning, critical
    auto_resolved: bool = False


@dataclass
class ReconciliationReport:
    """Summary of a reconciliation run."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    broker_positions: int = 0
    db_open_trades: int = 0
    issues: list[ReconciliationIssue] = field(default_factory=list)
    reconciled: bool = False

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    @property
    def issue_count(self) -> int:
        return len(self.issues)


class PositionReconciler:
    """
    Reconciles broker-side positions with DB trade records.

    Detects:
    - Orphaned broker positions (open at broker, no DB record)
    - Orphaned DB records (open in DB, closed/missing at broker)
    - SL/TP mismatches between broker and DB
    - Volume mismatches
    """

    def __init__(
        self,
        executor,
        trade_repo=None,
        *,
        tolerance_price: float = 0.01,
        tolerance_volume: float = 0.001,
    ):
        self.executor = executor
        self.trade_repo = trade_repo
        self.tolerance_price = tolerance_price
        self.tolerance_volume = tolerance_volume

    async def reconcile(self, instance_id: str = "") -> ReconciliationReport:
        """
        Run full reconciliation between broker and DB.

        Returns a report with any discrepancies found.
        """
        report = ReconciliationReport()

        # 1. Get broker-side positions
        try:
            broker_positions = await self.executor.get_open_positions()
            report.broker_positions = len(broker_positions)
        except Exception as e:
            logger.error("[reconciler] Failed to get broker positions: %s", e)
            report.issues.append(
                ReconciliationIssue(
                    ticket=0,
                    symbol="",
                    issue_type="broker_error",
                    description=f"Failed to query broker: {e}",
                    severity="critical",
                )
            )
            return report

        # 2. Get DB-side open trades
        db_trades: list = []
        if self.trade_repo:
            try:
                db_trades = await self.trade_repo.get_open_trades(
                    instance_id=instance_id
                )
                report.db_open_trades = len(db_trades)
            except Exception as e:
                logger.error("[reconciler] Failed to get DB trades: %s", e)
                report.issues.append(
                    ReconciliationIssue(
                        ticket=0,
                        symbol="",
                        issue_type="db_error",
                        description=f"Failed to query DB: {e}",
                        severity="critical",
                    )
                )
                return report

        # 3. Build lookup maps
        broker_by_ticket = {p.ticket: p for p in broker_positions}
        db_by_ticket = {}
        for t in db_trades:
            # Phase 2D: use explicit attribute, not getattr fallback
            ticket = t.order_ticket
            if ticket:
                db_by_ticket[ticket] = t
            else:
                logger.warning(
                    "[reconciler] OPEN trade id=%s has no order_ticket — "
                    "cannot match to broker position",
                    getattr(t, "id", "?"),
                )

        # 4. Check for orphaned broker positions (at broker but not in DB)
        for ticket, pos in broker_by_ticket.items():
            if ticket not in db_by_ticket:
                report.issues.append(
                    ReconciliationIssue(
                        ticket=ticket,
                        symbol=pos.symbol,
                        issue_type="orphaned_broker",
                        description=(
                            f"Position at broker (ticket={ticket}, {pos.direction} "
                            f"{pos.volume} lots @ {pos.entry_price}) has no DB record. "
                            f"May be from a crash or manual trade."
                        ),
                        severity="critical",
                    )
                )

        # 5. Check for orphaned DB records (in DB but not at broker)
        for ticket, trade in db_by_ticket.items():
            if ticket not in broker_by_ticket:
                report.issues.append(
                    ReconciliationIssue(
                        ticket=ticket,
                        symbol=getattr(trade, "symbol", ""),
                        issue_type="orphaned_db",
                        description=(
                            f"Trade in DB (ticket={ticket}) marked OPEN but not found "
                            f"at broker. Position may have been closed externally."
                        ),
                        severity="warning",
                    )
                )

        # 6. Check for SL/TP/volume mismatches
        for ticket in set(broker_by_ticket) & set(db_by_ticket):
            pos = broker_by_ticket[ticket]
            trade = db_by_ticket[ticket]

            # Volume mismatch
            db_vol = getattr(trade, "lot_size", 0) or 0
            if abs(pos.volume - db_vol) > self.tolerance_volume:
                report.issues.append(
                    ReconciliationIssue(
                        ticket=ticket,
                        symbol=pos.symbol,
                        issue_type="volume_mismatch",
                        description=(
                            f"Volume mismatch: broker={pos.volume}, DB={db_vol}"
                        ),
                        severity="warning",
                    )
                )

            # SL mismatch
            db_sl = getattr(trade, "stop_loss", 0) or 0
            if abs(pos.stop_loss - db_sl) > self.tolerance_price:
                report.issues.append(
                    ReconciliationIssue(
                        ticket=ticket,
                        symbol=pos.symbol,
                        issue_type="sl_mismatch",
                        description=(
                            f"SL mismatch: broker={pos.stop_loss}, DB={db_sl}"
                        ),
                        severity="warning",
                    )
                )

            # TP mismatch
            db_tp = getattr(trade, "take_profit_1", 0) or 0
            if abs(pos.take_profit - db_tp) > self.tolerance_price:
                report.issues.append(
                    ReconciliationIssue(
                        ticket=ticket,
                        symbol=pos.symbol,
                        issue_type="tp_mismatch",
                        description=(
                            f"TP mismatch: broker={pos.take_profit}, DB={db_tp}"
                        ),
                        severity="warning",
                    )
                )

        # 7. C-01: Detect stale PENDING trade_logs (pre-broker write, never confirmed)
        if self.trade_repo:
            try:
                stale_pending = await self.trade_repo.get_pending_trades(
                    instance_id=instance_id or None,
                    older_than_minutes=5,
                )
                for trade in stale_pending:
                    ticket = trade.order_ticket
                    # If broker has a matching position, this PENDING was just never
                    # confirmed — promote it to OPEN via the reconciler.
                    if ticket and ticket in broker_by_ticket:
                        pos = broker_by_ticket[ticket]
                        report.issues.append(
                            ReconciliationIssue(
                                ticket=ticket or 0,
                                symbol=trade.symbol or "",
                                issue_type="pending_promoted",
                                description=(
                                    f"PENDING trade id={trade.id} matched broker position "
                                    f"ticket={ticket} — promoting to OPEN."
                                ),
                                severity="warning",
                                auto_resolved=True,
                            )
                        )
                        logger.warning(
                            "[reconciler] Promoting stale PENDING trade id=%d "
                            "ticket=%s to OPEN",
                            trade.id, ticket,
                        )
                        trade.outcome = "OPEN"
                        await self.trade_repo._session.flush()
                    else:
                        report.issues.append(
                            ReconciliationIssue(
                                ticket=ticket or 0,
                                symbol=trade.symbol or "",
                                issue_type="orphaned_pending",
                                description=(
                                    f"PENDING trade id={trade.id} is >5min old with no "
                                    f"broker position match — possible crash during execution."
                                ),
                                severity="critical",
                            )
                        )
                        logger.critical(
                            "[reconciler] Orphaned PENDING trade id=%d (no broker match) "
                            "— manual review required",
                            trade.id,
                        )
            except Exception as pending_err:
                logger.warning("[reconciler] PENDING trade check failed: %s", pending_err)

        # Phase 2E: only mark reconciled if no critical issues found
        report.reconciled = not report.has_critical

        # Log summary
        if report.issues:
            level = logging.CRITICAL if report.has_critical else logging.WARNING
            logger.log(
                level,
                "[reconciler] Found %d issues (%d critical) across %d broker / %d DB positions",
                report.issue_count,
                sum(1 for i in report.issues if i.severity == "critical"),
                report.broker_positions,
                report.db_open_trades,
            )
            for issue in report.issues:
                logger.log(
                    logging.CRITICAL if issue.severity == "critical" else logging.WARNING,
                    "[reconciler] %s: %s",
                    issue.issue_type,
                    issue.description,
                )
        else:
            logger.info(
                "[reconciler] Reconciliation OK — %d broker / %d DB positions match",
                report.broker_positions,
                report.db_open_trades,
            )

        return report
