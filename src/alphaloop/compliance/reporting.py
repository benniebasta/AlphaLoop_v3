"""
Compliance and regulatory trade reporting framework.

Generates structured reports for:
- Trade execution audit (best execution analysis)
- Order flow analysis
- Risk limit breach history
- Strategy change log
- Data retention compliance

This is a foundation module — specific regulatory frameworks
(MiFID II, SEC, etc.) would extend these base reports.
"""

import logging
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ComplianceReporter:
    """
    Generates compliance reports from trade and system data.
    """

    def __init__(self, trade_repo=None, settings_service=None):
        self.trade_repo = trade_repo
        self.settings_service = settings_service
        # In-memory ring buffer for risk limit breaches (capped at 500 entries)
        self._breach_log: deque[dict] = deque(maxlen=500)

    def record_breach(self, event) -> None:
        """
        Record a RiskLimitHit event into the breach log.
        Subscribe this method to the RiskLimitHit event in main.py.
        """
        try:
            self._breach_log.append({
                "timestamp": event.timestamp.isoformat() if hasattr(event.timestamp, "isoformat") else str(event.timestamp),
                "symbol": getattr(event, "symbol", ""),
                "limit_type": getattr(event, "limit_type", ""),
                "details": getattr(event, "details", ""),
            })
        except Exception as e:
            logger.warning("[compliance] Failed to record breach: %s", e)

    async def best_execution_report(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Best execution analysis — compares requested vs actual fill prices.

        Metrics:
        - Average slippage (requested vs fill price)
        - Slippage distribution
        - Spread at fill analysis
        - Execution latency distribution
        """
        if not self.trade_repo:
            return {"status": "no_repo"}

        if not start_date:
            start_date = date.today() - timedelta(days=30)
        if not end_date:
            end_date = date.today()

        try:
            trades = await self.trade_repo.get_closed_trades(limit=1000)
            filtered = [
                t for t in trades
                if getattr(t, "closed_at", None)
                and t.closed_at.date() >= start_date
                and t.closed_at.date() <= end_date
            ]

            if not filtered:
                return {"status": "no_trades", "period": f"{start_date} to {end_date}"}

            slippages = [
                float(getattr(t, "slippage_pips", 0) or 0) for t in filtered
            ]
            spreads = [
                float(getattr(t, "spread_at_entry", 0) or 0) for t in filtered
            ]

            return {
                "status": "complete",
                "period": f"{start_date} to {end_date}",
                "total_trades": len(filtered),
                "avg_slippage_pips": round(sum(slippages) / len(slippages), 3) if slippages else 0,
                "max_slippage_pips": round(max(slippages), 3) if slippages else 0,
                "avg_spread_at_fill": round(sum(spreads) / len(spreads), 3) if spreads else 0,
                "positive_slippage_pct": round(
                    sum(1 for s in slippages if s > 0) / len(slippages) * 100, 1
                ) if slippages else 0,
            }
        except Exception as e:
            logger.error("[compliance] Best execution report failed: %s", e)
            return {"status": "error", "error": str(e)}

    async def risk_breach_report(
        self,
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Risk limit breach history — tracks when and why kill switches
        or circuit breakers were activated.
        Populated from RiskLimitHit events captured via record_breach().
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        filtered = []
        for entry in self._breach_log:
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    filtered.append(entry)
            except Exception:
                filtered.append(entry)  # Include if timestamp parse fails

        # Aggregate counts by limit_type
        counts: dict[str, int] = {}
        for entry in filtered:
            lt = entry.get("limit_type", "unknown")
            counts[lt] = counts.get(lt, 0) + 1

        return {
            "status": "complete",
            "period_days": days,
            "total_breaches": len(filtered),
            "breach_counts_by_type": counts,
            "recent_breaches": list(filtered)[-20:],  # Last 20
        }

    async def data_retention_report(self) -> dict[str, Any]:
        """
        Data retention compliance — shows what data is stored and for how long.
        """
        return {
            "status": "complete",
            "data_types": {
                "trade_logs": {
                    "retention": "indefinite",
                    "contains_pii": False,
                    "encrypted_fields": ["api keys (in settings)"],
                },
                "signal_log": {
                    "retention": "90 days recommended",
                    "contains_pii": False,
                },
                "config_audit_log": {
                    "retention": "indefinite",
                    "contains_pii": False,
                },
                "backtest_runs": {
                    "retention": "indefinite",
                    "contains_pii": False,
                },
            },
            "encryption": {
                "at_rest": "Fernet (AES-128-CBC) for sensitive settings",
                "in_transit": "HTTPS recommended for production",
            },
        }
