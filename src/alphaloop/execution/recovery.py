"""Startup recovery for non-terminal order records."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class OrderRecoveryIssue:
    order_id: str
    issue_type: str
    description: str
    severity: str = "critical"
    broker_ticket: int | None = None
    client_order_id: str | None = None
    auto_resolved: bool = False


@dataclass
class OrderRecoveryReport:
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_orders: int = 0
    resolved_orders: int = 0
    unresolved_orders: int = 0
    issues: list[OrderRecoveryIssue] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(issue.severity == "critical" for issue in self.issues)

    def to_payload(self, *, stage: str) -> dict:
        return {
            "stage": stage,
            "total_orders": self.total_orders,
            "resolved_orders": self.resolved_orders,
            "unresolved_orders": self.unresolved_orders,
            "has_critical": self.has_critical,
            "issues": [
                {
                    "order_id": issue.order_id,
                    "issue_type": issue.issue_type,
                    "description": issue.description,
                    "severity": issue.severity,
                    "broker_ticket": issue.broker_ticket,
                    "client_order_id": issue.client_order_id,
                    "auto_resolved": issue.auto_resolved,
                }
                for issue in self.issues
            ],
        }


class OrderRecoveryWorker:
    """Resolve startup non-terminal orders using broker-visible open positions."""

    def __init__(self, *, executor, order_repo) -> None:
        self.executor = executor
        self.order_repo = order_repo

    async def recover_startup_orders(self, *, instance_id: str = "") -> OrderRecoveryReport:
        report = OrderRecoveryReport()
        records = await self.order_repo.get_non_terminal(instance_id=instance_id or None)
        report.total_orders = len(records)
        if not records:
            return report

        try:
            broker_positions = await self.executor.get_open_positions()
        except Exception as exc:
            description = f"Startup order recovery failed to query broker positions: {exc}"
            report.unresolved_orders = len(records)
            report.issues.append(
                OrderRecoveryIssue(
                    order_id="*",
                    issue_type="broker_query_failed",
                    description=description,
                    severity="critical",
                )
            )
            logger.critical("[order-recovery] %s", description)
            return report

        positions_by_ticket = {position.ticket: position for position in broker_positions}

        for record in records:
            matching_position = None
            if record.broker_ticket:
                matching_position = positions_by_ticket.get(record.broker_ticket)

            if matching_position is not None:
                await self.order_repo.update_status(
                    record.order_id,
                    "FILLED",
                    broker_ticket=matching_position.ticket,
                    fill_price=matching_position.entry_price,
                    fill_volume=matching_position.volume,
                    error_message=None,
                )
                report.resolved_orders += 1
                report.issues.append(
                    OrderRecoveryIssue(
                        order_id=record.order_id,
                        issue_type="broker_match_promoted",
                        description=(
                            f"Recovered order {record.order_id} from broker ticket "
                            f"{matching_position.ticket} during startup."
                        ),
                        severity="warning",
                        broker_ticket=matching_position.ticket,
                        client_order_id=record.client_order_id,
                        auto_resolved=True,
                    )
                )
                logger.warning(
                    "[order-recovery] Recovered order %s via broker ticket %s",
                    record.order_id,
                    matching_position.ticket,
                )
                continue

            if record.broker_ticket:
                description = (
                    f"Order {record.order_id} has broker ticket {record.broker_ticket} "
                    "but no matching open broker position."
                )
            else:
                description = (
                    f"Order {record.order_id} has no broker ticket and cannot be "
                    "verified after restart."
                )

            if record.status != "RECOVERY_PENDING":
                await self.order_repo.update_status(
                    record.order_id,
                    "RECOVERY_PENDING",
                    error_message=description,
                )
            else:
                await self.order_repo.set_error_message(record.order_id, description)

            report.unresolved_orders += 1
            report.issues.append(
                OrderRecoveryIssue(
                    order_id=record.order_id,
                    issue_type="recovery_pending",
                    description=description,
                    severity="critical",
                    broker_ticket=record.broker_ticket,
                    client_order_id=record.client_order_id,
                )
            )
            logger.critical("[order-recovery] %s", description)

        return report
