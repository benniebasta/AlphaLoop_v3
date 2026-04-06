"""GET /api/risk — Risk monitor status computed from trade history."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.core.container import Container
from alphaloop.db.models.trade import TradeLog
from alphaloop.risk.service import RiskService
from alphaloop.supervision.service import SupervisionService
from alphaloop.webui.deps import get_container, get_db_session
from alphaloop.webui.routes.controls import _build_risk_lock_state

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("")
async def get_risk_status(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Compute risk metrics from trade history, including VaR/CVaR."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Daily trades
    daily_q = select(TradeLog).where(TradeLog.opened_at >= today_start)
    daily_trades = list((await session.execute(daily_q)).scalars())

    daily_pnl = sum(
        t.pnl_usd or 0 for t in daily_trades if t.outcome in ("WIN", "LOSS", "BE")
    )
    daily_wins = sum(1 for t in daily_trades if t.outcome == "WIN")
    daily_losses = sum(1 for t in daily_trades if t.outcome == "LOSS")
    open_count = sum(1 for t in daily_trades if t.outcome == "OPEN")

    # Consecutive losses (scan recent closed trades)
    recent_q = (
        select(TradeLog)
        .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
        .order_by(TradeLog.closed_at.desc())
        .limit(20)
    )
    recent = list((await session.execute(recent_q)).scalars())
    consec = 0
    for t in recent:
        if t.outcome == "LOSS":
            consec += 1
        else:
            break

    # Historical VaR/CVaR — compute from trade PnL history
    var_95 = None
    cvar_95 = None
    var_99 = None
    var_observations = 0
    var_breach_today = False
    try:
        from alphaloop.risk.var_calculator import HistoricalVaRCalculator
        hist_q = (
            select(TradeLog.pnl_usd)
            .where(TradeLog.outcome.in_(["WIN", "LOSS", "BE"]))
            .where(TradeLog.pnl_usd.isnot(None))
            .order_by(TradeLog.closed_at.desc())
            .limit(1000)
        )
        pnl_rows = list((await session.execute(hist_q)).scalars())
        pnl_series = [float(p) for p in reversed(pnl_rows)]  # chronological order
        if pnl_series:
            calc = HistoricalVaRCalculator(confidence_level=0.95)
            calc.fit(pnl_series)
            var_95 = calc.var()
            cvar_95 = calc.cvar()
            var_99 = calc.var(0.99)
            var_observations = calc.observation_count
            var_breach_today = calc.var_breach(daily_pnl)
    except Exception:
        pass  # VaR is advisory — never block the main response

    return {
        "daily_pnl": round(daily_pnl, 2),
        "daily_trades": len(daily_trades),
        "daily_wins": daily_wins,
        "daily_losses": daily_losses,
        "open_positions": open_count,
        "consecutive_losses": consec,
        "daily_win_rate": round(
            daily_wins / max(daily_wins + daily_losses, 1) * 100, 1
        ),
        # Probabilistic risk
        "var_95": var_95,
        "cvar_95": cvar_95,
        "var_99": var_99,
        "var_observations": var_observations,
        "var_breach_today": var_breach_today,
        "timestamp": now.isoformat(),
    }


@router.get("/portfolio")
async def get_portfolio_snapshot(
    container: Container = Depends(get_container),
) -> dict:
    service = getattr(container, "risk_service", None) or RiskService(container.db_session_factory)
    snapshot = await service.get_portfolio_snapshot()
    supervision = getattr(container, "supervision_service", None) or SupervisionService(
        container.db_session_factory
    )
    return {
        **snapshot.to_dict(),
        "guard_state": await _build_risk_lock_state(supervision),
    }


@router.get("/stress")
async def get_stress_scenarios(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Run built-in stress scenarios against current account balance.

    Returns per-scenario simulated loss, final equity, and margin call risk.
    """
    try:
        from alphaloop.risk.stress_tester import StressTester

        # Estimate current balance from recent trade history
        # (uses sum of all closed PnL + a base of 10k if no data)
        all_q = select(TradeLog.pnl_usd).where(
            TradeLog.outcome.in_(["WIN", "LOSS", "BE"]),
            TradeLog.pnl_usd.isnot(None),
        )
        all_pnl = list((await session.execute(all_q)).scalars())
        total_pnl = sum(float(p) for p in all_pnl)
        base_balance = 10_000.0  # default starting capital
        estimated_balance = max(base_balance + total_pnl, 100.0)

        # Count open positions
        open_q = select(TradeLog.lot_size).where(TradeLog.outcome == "OPEN")
        open_lots = list((await session.execute(open_q)).scalars())
        open_lot_exposure = sum(float(l or 0) for l in open_lots)

        tester = StressTester()
        results = tester.run_all(
            current_balance=round(estimated_balance, 2),
            open_lot_exposure=open_lot_exposure,
        )

        return {
            "estimated_balance": round(estimated_balance, 2),
            "open_lot_exposure": round(open_lot_exposure, 4),
            "scenarios": results,
        }

    except Exception as e:
        return {"error": str(e), "scenarios": []}
