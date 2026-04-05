"""Portfolio-risk service and snapshot types."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from alphaloop.db.repositories.trade_repo import TradeRepository
from alphaloop.utils.time import utc_day_start


@dataclass(slots=True)
class PortfolioSnapshot:
    gross_risk_usd: float
    net_risk_usd: float
    heat_pct: float
    positions_by_symbol: dict[str, int]
    positions_by_direction: dict[str, int]
    daily_pnl_utc: float
    stress_loss_1x: float
    stress_loss_2x: float
    open_positions: int
    estimated_balance: float

    def to_dict(self) -> dict:
        return asdict(self)


class RiskService:
    """Computes portfolio-wide risk facts from persisted trades."""

    def __init__(self, session_factory, *, base_balance: float = 10_000.0) -> None:
        self._session_factory = session_factory
        self._base_balance = base_balance

    async def get_portfolio_snapshot(self) -> PortfolioSnapshot:
        async with self._session_factory() as session:
            trade_repo = TradeRepository(session)
            open_trades = await trade_repo.get_open_trades()
            closed_today = await trade_repo.get_closed_trades(
                since=utc_day_start(),
                limit=2000,
            )
            closed_all = await trade_repo.get_closed_trades(limit=5000)

        estimated_balance = max(
            self._base_balance + sum(float(t.pnl_usd or 0.0) for t in closed_all),
            100.0,
        )
        gross_risk = sum(float(t.risk_amount_usd or 0.0) for t in open_trades)
        signed_risk = 0.0
        symbol_counts: Counter[str] = Counter()
        direction_counts: Counter[str] = Counter()
        for trade in open_trades:
            direction = (trade.direction or "BUY").upper()
            risk = float(trade.risk_amount_usd or 0.0)
            signed_risk += risk if direction == "BUY" else -risk
            symbol_counts[trade.symbol or "UNKNOWN"] += 1
            direction_counts[direction] += 1

        heat_pct = gross_risk / estimated_balance if estimated_balance > 0 else 0.0
        daily_pnl = sum(float(t.pnl_usd or 0.0) for t in closed_today)

        return PortfolioSnapshot(
            gross_risk_usd=round(gross_risk, 2),
            net_risk_usd=round(signed_risk, 2),
            heat_pct=round(heat_pct, 4),
            positions_by_symbol=dict(symbol_counts),
            positions_by_direction=dict(direction_counts),
            daily_pnl_utc=round(daily_pnl, 2),
            stress_loss_1x=round(gross_risk, 2),
            stress_loss_2x=round(gross_risk * 2, 2),
            open_positions=len(open_trades),
            estimated_balance=round(estimated_balance, 2),
        )
