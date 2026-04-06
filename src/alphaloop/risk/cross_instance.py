"""
Cross-instance risk aggregation.

Provides a shared risk view across all running bot instances
by reading from the database (trade_logs and instances tables).

Each instance writes its trades to the shared DB. This module
reads aggregate risk across ALL instances to enforce portfolio-level
caps that no single instance can exceed.
"""

import logging
from datetime import datetime, timezone

from alphaloop.utils.time import utc_day_start

logger = logging.getLogger(__name__)


class CrossInstanceRiskAggregator:
    """
    Aggregates risk metrics across all running bot instances.

    Reads from the shared database to provide a portfolio-wide view.
    Each bot instance should check this before opening new trades.
    """

    def __init__(
        self,
        trade_repo=None,
        *,
        max_total_daily_loss_pct: float = 0.06,
        max_total_open_positions: int = 6,
        max_total_portfolio_heat_pct: float = 0.10,
        fail_open: bool = False,
    ):
        self.trade_repo = trade_repo
        self.max_total_daily_loss_pct = max_total_daily_loss_pct
        self.max_total_open_positions = max_total_open_positions
        self.max_total_portfolio_heat_pct = max_total_portfolio_heat_pct
        self._fail_open = fail_open

    async def get_aggregate_status(self, account_balance: float) -> dict:
        """Get aggregate risk status across all instances."""
        if not self.trade_repo:
            return {"available": False, "reason": "No trade repo configured"}

        try:
            # All open trades across all instances
            all_open = await self.trade_repo.get_open_trades()
            total_open = len(all_open)
            total_risk_usd = sum(
                float(getattr(t, "risk_amount_usd", 0) or 0) for t in all_open
            )

            # Today's closed trades across all instances
            today_start = utc_day_start()
            today_closed = await self.trade_repo.get_closed_trades(
                closed_since=today_start,
                limit=None,
            )
            total_daily_pnl = sum(
                float(getattr(t, "pnl_usd", 0) or 0) for t in today_closed
            )

            # Compute percentages
            daily_loss_pct = (
                abs(min(total_daily_pnl, 0)) / account_balance
                if account_balance > 0
                else 0
            )
            heat_pct = (
                total_risk_usd / account_balance if account_balance > 0 else 0
            )

            return {
                "available": True,
                "total_open_positions": total_open,
                "total_risk_usd": round(total_risk_usd, 2),
                "total_daily_pnl": round(total_daily_pnl, 2),
                "daily_loss_pct": round(daily_loss_pct, 4),
                "heat_pct": round(heat_pct, 4),
                "positions_by_symbol": self._group_by_symbol(all_open),
            }
        except Exception as e:
            logger.error("[cross-instance] Aggregation failed: %s", e)
            return {"available": False, "reason": str(e)}

    async def can_open_trade(
        self,
        account_balance: float,
        additional_risk_usd: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Check if a new trade is allowed under cross-instance portfolio limits.

        Returns (allowed, reason).
        """
        status = await self.get_aggregate_status(account_balance)

        if not status.get("available"):
            # Phase 3C: Default to fail-closed unless explicitly configured otherwise
            if self._fail_open:
                logger.warning(
                    "[cross-instance] Aggregation unavailable, allowing trade "
                    "(fail_open=True): %s",
                    status.get("reason"),
                )
                return True, ""
            else:
                logger.warning(
                    "[cross-instance] Aggregation unavailable — blocking trade "
                    "(fail_open=False): %s",
                    status.get("reason"),
                )
                return False, "Cross-instance risk aggregation unavailable"

        # Position count cap
        total_open = status["total_open_positions"]
        if total_open >= self.max_total_open_positions:
            return False, (
                f"Cross-instance position cap: {total_open} "
                f">= {self.max_total_open_positions} total positions"
            )

        # Daily loss cap
        if status["daily_loss_pct"] >= self.max_total_daily_loss_pct:
            return False, (
                f"Cross-instance daily loss: "
                f"{status['daily_loss_pct']*100:.1f}% "
                f">= {self.max_total_daily_loss_pct*100:.1f}%"
            )

        # Portfolio heat cap (including new trade)
        if account_balance > 0:
            projected_heat = (
                status["total_risk_usd"] + additional_risk_usd
            ) / account_balance
            if projected_heat >= self.max_total_portfolio_heat_pct:
                return False, (
                    f"Cross-instance heat cap: "
                    f"{projected_heat*100:.1f}% "
                    f">= {self.max_total_portfolio_heat_pct*100:.1f}%"
                )

        return True, ""

    @staticmethod
    def _group_by_symbol(trades) -> dict[str, int]:
        """Count open trades per symbol."""
        counts: dict[str, int] = {}
        for t in trades:
            sym = getattr(t, "symbol", "unknown")
            counts[sym] = counts.get(sym, 0) + 1
        return counts
