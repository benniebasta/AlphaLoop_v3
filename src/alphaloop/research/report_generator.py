"""
Automated Performance Report Generator.

Produces daily/weekly/monthly summaries from trade history.
Reports can be delivered via Telegram or viewed in the WebUI.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Literal

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

ReportPeriod = Literal["daily", "weekly", "monthly"]


class ReportGenerator:
    """Generates trading performance reports from DB trade history."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def generate(
        self,
        period: ReportPeriod = "daily",
        symbol: str | None = None,
    ) -> dict:
        """
        Generate a performance report for the given period.

        Returns a dict with: period, start/end dates, trade_count, win_rate,
        total_pnl, avg_pnl, best_trade, worst_trade, sharpe, max_dd,
        by_symbol breakdown, by_session breakdown.
        """
        from alphaloop.db.repositories.trade_repo import TradeRepository

        now = datetime.now(timezone.utc)
        if period == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "weekly":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # monthly
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        async with self._session_factory() as session:
            repo = TradeRepository(session)
            trades = await repo.get_closed_trades(
                symbol=symbol,
                since=start,
                limit=5000,
            )

        if not trades:
            return {
                "period": period,
                "start": start.isoformat(),
                "end": now.isoformat(),
                "trade_count": 0,
                "message": "No closed trades in this period",
            }

        pnl_list = [getattr(t, "pnl_usd", 0) or 0 for t in trades]
        wins = sum(1 for t in trades if getattr(t, "outcome", "") == "WIN")
        losses = sum(1 for t in trades if getattr(t, "outcome", "") == "LOSS")
        breakevens = sum(1 for t in trades if getattr(t, "outcome", "") == "BE")

        total_pnl = sum(pnl_list)
        avg_pnl = total_pnl / len(pnl_list) if pnl_list else 0
        best = max(pnl_list) if pnl_list else 0
        worst = min(pnl_list) if pnl_list else 0

        # Sharpe ratio
        arr = np.array(pnl_list)
        sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0

        # Max drawdown
        cumulative = np.cumsum(arr)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        # By symbol breakdown
        by_symbol: dict[str, dict] = {}
        for t in trades:
            sym = getattr(t, "symbol", "UNKNOWN")
            if sym not in by_symbol:
                by_symbol[sym] = {"count": 0, "pnl": 0, "wins": 0}
            by_symbol[sym]["count"] += 1
            by_symbol[sym]["pnl"] += getattr(t, "pnl_usd", 0) or 0
            if getattr(t, "outcome", "") == "WIN":
                by_symbol[sym]["wins"] += 1

        # By session breakdown
        by_session: dict[str, dict] = {}
        for t in trades:
            sess = getattr(t, "session_name", "unknown") or "unknown"
            if sess not in by_session:
                by_session[sess] = {"count": 0, "pnl": 0, "wins": 0}
            by_session[sess]["count"] += 1
            by_session[sess]["pnl"] += getattr(t, "pnl_usd", 0) or 0
            if getattr(t, "outcome", "") == "WIN":
                by_session[sess]["wins"] += 1

        return {
            "period": period,
            "start": start.isoformat(),
            "end": now.isoformat(),
            "trade_count": len(trades),
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(max_dd, 2),
            "by_symbol": {
                sym: {**data, "pnl": round(data["pnl"], 2)}
                for sym, data in by_symbol.items()
            },
            "by_session": {
                sess: {**data, "pnl": round(data["pnl"], 2)}
                for sess, data in by_session.items()
            },
        }

    async def format_telegram(self, report: dict) -> str:
        """Format a report as a Telegram-friendly text message."""
        if report.get("trade_count", 0) == 0:
            return f"📊 {report['period'].title()} Report: No trades"

        lines = [
            f"📊 *{report['period'].title()} Report*",
            f"Period: {report['start'][:10]} → {report['end'][:10]}",
            "",
            f"Trades: {report['trade_count']} (W:{report['wins']} L:{report['losses']} BE:{report['breakevens']})",
            f"Win Rate: {report['win_rate']}%",
            f"Total P&L: ${report['total_pnl']:+.2f}",
            f"Avg P&L: ${report['avg_pnl']:+.2f}",
            f"Best: ${report['best_trade']:+.2f} | Worst: ${report['worst_trade']:+.2f}",
            f"Sharpe: {report['sharpe']:.3f}",
            f"Max DD: ${report['max_drawdown']:.2f}",
        ]

        if report.get("by_symbol"):
            lines.append("\n*By Symbol:*")
            for sym, data in report["by_symbol"].items():
                wr = round(data["wins"] / max(data["count"], 1) * 100)
                lines.append(f"  {sym}: {data['count']} trades, ${data['pnl']:+.2f}, {wr}% WR")

        return "\n".join(lines)
