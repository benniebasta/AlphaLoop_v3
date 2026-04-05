"""
Risk filter — daily loss limit, drawdown check, kill switch.

Pipeline order: SIXTH (LAST of the core filters) — only run if all
other filters pass. Must have final authority.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


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
            can_open, reason = await monitor.can_open_trade()
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

    async def extract_features(self, context) -> FeatureResult:
        monitor = context.risk_monitor
        status = {}

        if monitor is not None:
            try:
                status = monitor.status if hasattr(monitor, "status") else {}
            except Exception:
                pass

        daily_pnl_pct = float(status.get("daily_pnl_pct", 0.0))
        daily_loss_limit = float(status.get("daily_loss_limit_pct", 3.0)) or 3.0
        consecutive_losses = int(status.get("consecutive_losses", 0))
        max_losses = int(status.get("max_consecutive_losses", 3)) or 3
        open_trades = int(status.get("open_trades", 0))
        max_trades = int(status.get("max_concurrent_trades", 3)) or 3
        kill_switch = bool(status.get("kill_switch", False))

        # risk_headroom: 100 = no risk used, 0 = at/past daily limit
        if kill_switch:
            risk_headroom = 0.0
        else:
            pnl_used = min(1.0, max(0.0, abs(min(daily_pnl_pct, 0.0)) / daily_loss_limit))
            loss_used = min(1.0, consecutive_losses / max_losses)
            trade_used = min(1.0, open_trades / max_trades)
            worst = max(pnl_used, loss_used, trade_used)
            risk_headroom = round((1.0 - worst) * 100, 1)

        return FeatureResult(
            group="volatility",
            features={"risk_headroom": risk_headroom},
            meta={
                "daily_pnl_pct": daily_pnl_pct,
                "consecutive_losses": consecutive_losses,
                "open_trades": open_trades,
                "kill_switch": kill_switch,
            },
        )
