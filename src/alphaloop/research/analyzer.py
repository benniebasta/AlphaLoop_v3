"""
research/analyzer.py — Async performance analysis.

Reviews trade history, computes metrics, generates AI-driven improvement
suggestions, and persists research reports to the database.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Optional

from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.core.events import EventBus, ResearchCompleted
from alphaloop.core.types import TradeOutcome
from alphaloop.db.repositories.research_repo import ResearchRepository
from alphaloop.db.repositories.trade_repo import TradeRepository
from alphaloop.research.prompts import RESEARCH_ANALYST_SYSTEM, RESEARCH_ANALYST_USER

logger = logging.getLogger(__name__)

# Type alias for the AI callback: (system, user) -> response text
AICallback = Callable[[str, str], Coroutine[Any, Any, str]]

MIN_TRADES_FOR_ANALYSIS = 10


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def compute_sharpe(pnl_list: list[float], risk_free_rate: float = 0.05) -> float | None:
    """Annualized Sharpe ratio from trade P&L values."""
    if len(pnl_list) < 10:
        return None
    import numpy as np
    arr = np.array(pnl_list, dtype=float)
    mean_abs = np.abs(arr).mean()
    if mean_abs == 0:
        return None
    returns = arr / mean_abs
    excess = returns - (risk_free_rate / 252)
    std = float(np.std(excess, ddof=1))
    if std == 0:
        return None
    return round(float(np.mean(excess) / std * (252 ** 0.5)), 3)


def _sanitize(obj: Any) -> Any:
    """Recursively convert values to JSON-safe primitives."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    try:
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            v = float(obj)
            return None if (math.isnan(v) or math.isinf(v)) else v
        if isinstance(obj, np.bool_):
            return bool(obj)
    except ImportError:
        pass
    return str(obj)


def _detect_degradation(
    pnl_list: list[float], window: int = 30
) -> dict[str, Any]:
    """Compare recent N trades vs previous N to detect Sharpe degradation."""
    if len(pnl_list) < window * 2:
        return {"status": "insufficient_data"}
    recent = pnl_list[-window:]
    previous = pnl_list[-(window * 2) : -window]
    recent_sharpe = compute_sharpe(recent)
    prev_sharpe = compute_sharpe(previous)
    if recent_sharpe is None or prev_sharpe is None or prev_sharpe == 0:
        return {
            "status": "unknown",
            "recent_sharpe": recent_sharpe,
            "previous_sharpe": prev_sharpe,
        }
    ratio = recent_sharpe / prev_sharpe
    return {
        "status": "degrading" if ratio < 0.7 else "stable",
        "recent_sharpe": recent_sharpe,
        "previous_sharpe": prev_sharpe,
        "ratio": round(ratio, 3),
    }


# ---------------------------------------------------------------------------
# Core metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute the full performance metrics suite from a list of trade dicts.

    Each dict is expected to have: outcome, pnl_usd, pnl_r, setup_type,
    session_name, opened_at, risk_amount_usd (all optional but ideal).
    """
    if not trades:
        return {}

    closed = [
        t for t in trades
        if t.get("outcome") in (TradeOutcome.WIN, TradeOutcome.LOSS, TradeOutcome.BREAKEVEN)
    ]
    if not closed:
        return {"total_trades": 0, "open_trades": len(trades)}

    pnl_usd = [t.get("pnl_usd", 0.0) or 0.0 for t in closed]
    pnl_r = [t.get("pnl_r", 0.0) or 0.0 for t in closed]

    wins = [t for t in closed if t.get("outcome") == TradeOutcome.WIN]
    losses = [t for t in closed if t.get("outcome") == TradeOutcome.LOSS]

    win_rate = len(wins) / len(closed) if closed else 0.0
    avg_win_r = sum(t.get("pnl_r", 0) or 0 for t in wins) / max(len(wins), 1)
    avg_loss_r = sum(t.get("pnl_r", 0) or 0 for t in losses) / max(len(losses), 1)
    expectancy = (win_rate * avg_win_r) + ((1 - win_rate) * avg_loss_r)

    # Drawdown
    import numpy as np
    cum = np.cumsum(pnl_usd)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = float(dd.min())

    # Profit factor
    gross_profit = sum(p for p in pnl_usd if p > 0)
    gross_loss = abs(sum(p for p in pnl_usd if p < 0))
    pf = min(9999.99, round(gross_profit / gross_loss, 3)) if gross_loss > 0 else (
        9999.99 if gross_profit > 0 else 0.0
    )

    # Setup breakdown
    setup_stats: dict[str, Any] = {}
    for t in closed:
        st = t.get("setup_type", "unknown") or "unknown"
        if st not in setup_stats:
            setup_stats[st] = {"count": 0, "wins": 0, "pnl_r_sum": 0.0, "pnl_usd_sum": 0.0}
        setup_stats[st]["count"] += 1
        if t.get("outcome") == TradeOutcome.WIN:
            setup_stats[st]["wins"] += 1
        setup_stats[st]["pnl_r_sum"] += t.get("pnl_r", 0) or 0
        setup_stats[st]["pnl_usd_sum"] += t.get("pnl_usd", 0) or 0
    for st, s in setup_stats.items():
        s["win_rate"] = round(s["wins"] / max(s["count"], 1), 3)
        s["avg_rr"] = round(s["pnl_r_sum"] / max(s["count"], 1), 2)
        s["total_pnl"] = round(s["pnl_usd_sum"], 2)
        del s["wins"], s["pnl_r_sum"], s["pnl_usd_sum"]

    raw = {
        "total_trades": len(closed),
        "win_rate": round(max(0.0, min(1.0, win_rate)), 3),
        "avg_win_r": round(avg_win_r, 3),
        "avg_loss_r": round(avg_loss_r, 3),
        "expectancy_r": round(expectancy, 3),
        "total_pnl_usd": round(sum(pnl_usd), 2),
        "max_drawdown_usd": round(max_dd, 2),
        "sharpe_ratio": compute_sharpe(pnl_usd),
        "profit_factor": pf,
        "setup_stats": setup_stats,
        "degradation": _detect_degradation(pnl_usd),
    }
    return _sanitize(raw)


# ---------------------------------------------------------------------------
# Research Analyzer
# ---------------------------------------------------------------------------

class ResearchAnalyzer:
    """
    Async research loop — analyzes trade performance and generates
    AI-driven improvement suggestions.

    Injected dependencies:
    - session_factory: SQLAlchemy async session factory
    - event_bus: for publishing ResearchCompleted events
    - ai_callback: async callable (system_prompt, user_prompt) -> response text
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        event_bus: EventBus,
        ai_callback: AICallback | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._ai_callback = ai_callback

    async def run(
        self,
        symbol: str | None = None,
        strategy_version: str | None = None,
        instance_id: str | None = None,
        lookback_days: int = 30,
    ) -> dict[str, Any] | None:
        """
        Main entry point. Loads trades, computes metrics, runs AI analysis,
        and persists the report.

        Returns the report dict or None if insufficient data.
        """
        tag = f"{symbol or 'ALL'} {strategy_version or 'auto'}"
        logger.info("Starting research analysis [%s] (lookback: %dd)", tag, lookback_days)

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        async with self._session_factory() as session:
            trade_repo = TradeRepository(session)
            trades = await trade_repo.get_closed_trades(
                symbol=symbol,
                strategy_version=strategy_version,
                instance_id=instance_id,
                since=cutoff,
                limit=2000,
            )
            if len(trades) < MIN_TRADES_FOR_ANALYSIS:
                logger.info(
                    "Not enough trades for research (%d < %d) — skipping",
                    len(trades), MIN_TRADES_FOR_ANALYSIS,
                )
                return None

            # Convert ORM objects to dicts
            trade_dicts = [
                {c.name: getattr(t, c.name) for c in t.__table__.columns}
                for t in trades
            ]

            metrics = compute_metrics(trade_dicts)
            analysis = await self._ai_analysis(metrics)

            # Persist report
            research_repo = ResearchRepository(session)
            now = datetime.now(timezone.utc)
            report = await research_repo.create_report(
                symbol=symbol,
                strategy_version=strategy_version or "auto",
                report_date=now,
                period_start=cutoff,
                period_end=now,
                total_trades=metrics.get("total_trades", 0),
                win_rate=metrics.get("win_rate"),
                avg_rr=metrics.get("expectancy_r"),
                total_pnl_usd=metrics.get("total_pnl_usd"),
                sharpe_ratio=metrics.get("sharpe_ratio"),
                max_drawdown_pct=metrics.get("max_drawdown_usd"),
                setup_stats=metrics.get("setup_stats", {}),
                session_stats={},
                hourly_stats={},
                analysis_summary=analysis.get("summary", ""),
                improvement_suggestions=analysis.get("improvement_suggestions", []),
                ai_confidence=analysis.get("confidence"),
                raw_metrics=metrics,
            )
            await session.commit()

            logger.info(
                "Research complete: %d trades | WR: %.1f%% | Sharpe: %s",
                metrics.get("total_trades", 0),
                (metrics.get("win_rate", 0) or 0) * 100,
                metrics.get("sharpe_ratio"),
            )

        # Publish event (outside session scope)
        await self._event_bus.publish(
            ResearchCompleted(symbol=symbol or "", report_id=report.id)
        )

        return {
            "report_id": report.id,
            "metrics": metrics,
            "analysis": analysis,
        }

    async def _ai_analysis(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Call AI to analyze metrics and generate improvement suggestions."""
        if self._ai_callback is None:
            return {
                "summary": "AI analysis unavailable — no callback configured",
                "improvement_suggestions": [],
            }

        user_prompt = RESEARCH_ANALYST_USER.format(
            metrics_json=json.dumps(metrics, indent=2, default=str)
        )

        try:
            raw = await self._ai_callback(RESEARCH_ANALYST_SYSTEM, user_prompt)
            if raw:
                clean = re.sub(r"```(?:json)?\s*", "", raw).strip()
                match = re.search(r"\{.*\}", clean, re.DOTALL)
                if match:
                    return json.loads(match.group())
        except Exception as exc:
            logger.error("AI research analysis failed: %s", exc)

        return {"summary": "Analysis unavailable", "improvement_suggestions": []}

    async def check_retraining_needed(
        self,
        symbol: str,
        strategy_version: str | None = None,
        degradation_threshold: float = 0.7,
        min_trades: int = 60,
    ) -> dict[str, Any]:
        """
        Check if a strategy needs retraining based on performance degradation.

        Compares recent Sharpe ratio vs previous window. If ratio drops below
        threshold for the last window of trades, flags for retraining.

        Returns:
            dict with "needs_retraining", "degradation_status", "trigger_action".
        """
        async with self._session_factory() as session:
            repo = TradeRepository(session)
            trades = await repo.get_closed_trades(symbol=symbol, limit=min_trades * 2)

        if len(trades) < min_trades:
            return {
                "needs_retraining": False,
                "reason": f"Insufficient trades ({len(trades)} < {min_trades})",
                "degradation_status": None,
            }

        pnl_list = [
            getattr(t, "pnl_usd", 0) or 0 for t in trades
        ]
        # Reverse to chronological order (trades are desc by default)
        pnl_list.reverse()

        deg = _detect_degradation(pnl_list, window=min(30, len(pnl_list) // 2))

        needs_retrain = deg.get("status") == "degrading"
        ratio = deg.get("ratio", 1.0)

        result = {
            "needs_retraining": needs_retrain,
            "degradation_status": deg,
            "symbol": symbol,
            "strategy_version": strategy_version,
            "total_trades_analyzed": len(pnl_list),
        }

        if needs_retrain:
            logger.warning(
                "RETRAINING NEEDED: %s — Sharpe degraded to %.1f%% of baseline "
                "(recent=%.3f, prev=%.3f)",
                symbol,
                (ratio or 0) * 100,
                deg.get("recent_sharpe", 0),
                deg.get("previous_sharpe", 0),
            )
            result["trigger_action"] = "queue_seedlab"
            result["reason"] = (
                f"Sharpe ratio degraded to {ratio:.0%} of previous window "
                f"(recent={deg.get('recent_sharpe')}, prev={deg.get('previous_sharpe')})"
            )
        else:
            result["trigger_action"] = None
            result["reason"] = f"Strategy performance stable (ratio={ratio})"

        return result
