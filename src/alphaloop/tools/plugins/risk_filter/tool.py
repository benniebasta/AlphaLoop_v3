"""
Risk filter — daily loss limit, drawdown check, kill switch.

Pipeline order: SIXTH (LAST of the core filters) — only run if all
other filters pass. Must have final authority.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult


class RiskFilter(BaseTool):
    """
    Risk monitor gate.

    Delegates to the RiskMonitor instance in context.risk_monitor.
    Blocks trades when kill switch is active, max concurrent trades
    reached, or daily loss limit exceeded.

    If risk_monitor is absent, fails safe (blocks).
    """

    name = "risk_filter"
    description = "Daily loss limit, drawdown check, kill switch enforcement"

    async def run(self, context) -> ToolResult:
        monitor = context.risk_monitor

        if monitor is None:
            return ToolResult(
                passed=False,
                reason="RiskMonitor missing from context — fail-safe block",
                severity="block",
                size_modifier=0.0,
            )

        try:
            can_open, reason = monitor.can_open_trade()
            status = monitor.status if hasattr(monitor, "status") else {}

            if not can_open:
                return ToolResult(
                    passed=False,
                    reason=reason,
                    severity="block",
                    size_modifier=0.0,
                    data=status,
                )

            return ToolResult(
                passed=True,
                reason=(
                    f"Risk OK — daily_pnl={status.get('daily_pnl_pct', 0):.1f}%, "
                    f"losses={status.get('consecutive_losses', 0)}, "
                    f"open={status.get('open_trades', 0)}"
                ),
                data=status,
            )
        except Exception as e:
            return ToolResult(
                passed=False,
                reason=f"Risk filter error ({e}) — fail-safe block",
                severity="block",
                size_modifier=0.0,
                data={"error": str(e)},
            )
