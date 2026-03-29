"""
Portfolio Manager — Multi-symbol risk coordination.

Tracks open positions across all active symbols and enforces
portfolio-wide risk constraints. Used by the trading loop to
check if a new trade is allowed given the portfolio state.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OpenPosition:
    symbol: str
    direction: str
    entry_price: float
    lot_size: float
    risk_usd: float
    opened_at: str = ""


class PortfolioManager:
    """Manages portfolio-wide risk across multiple symbols."""

    def __init__(
        self,
        max_total_positions: int = 5,
        max_portfolio_heat_pct: float = 6.0,
        account_balance: float = 10_000.0,
    ) -> None:
        self.max_total_positions = max_total_positions
        self.max_portfolio_heat_pct = max_portfolio_heat_pct
        self.account_balance = account_balance
        self._positions: dict[str, list[OpenPosition]] = {}

    def register_open(self, position: OpenPosition) -> None:
        """Register a newly opened position."""
        if position.symbol not in self._positions:
            self._positions[position.symbol] = []
        self._positions[position.symbol].append(position)
        logger.info("Portfolio: registered %s %s (risk=$%.2f)", position.direction, position.symbol, position.risk_usd)

    def register_close(self, symbol: str, entry_price: float) -> None:
        """Remove a closed position by symbol and entry price."""
        if symbol in self._positions:
            self._positions[symbol] = [
                p for p in self._positions[symbol]
                if abs(p.entry_price - entry_price) > 0.0001
            ]
            if not self._positions[symbol]:
                del self._positions[symbol]

    @property
    def total_positions(self) -> int:
        return sum(len(positions) for positions in self._positions.values())

    @property
    def total_risk_usd(self) -> float:
        return sum(
            p.risk_usd for positions in self._positions.values() for p in positions
        )

    @property
    def portfolio_heat_pct(self) -> float:
        if self.account_balance <= 0:
            return 0.0
        return (self.total_risk_usd / self.account_balance) * 100

    def can_open_trade(self, symbol: str, risk_usd: float) -> tuple[bool, str]:
        """Check if opening a new trade is allowed portfolio-wide."""
        if self.total_positions >= self.max_total_positions:
            return False, f"Max positions reached ({self.total_positions}/{self.max_total_positions})"

        projected_heat = ((self.total_risk_usd + risk_usd) / max(self.account_balance, 1)) * 100
        if projected_heat > self.max_portfolio_heat_pct:
            return False, f"Portfolio heat would exceed {self.max_portfolio_heat_pct}% ({projected_heat:.1f}%)"

        return True, "OK"

    def get_positions_for_symbol(self, symbol: str) -> list[OpenPosition]:
        return list(self._positions.get(symbol, []))

    def update_balance(self, new_balance: float) -> None:
        self.account_balance = new_balance

    @property
    def status(self) -> dict:
        return {
            "total_positions": self.total_positions,
            "total_risk_usd": round(self.total_risk_usd, 2),
            "portfolio_heat_pct": round(self.portfolio_heat_pct, 2),
            "max_positions": self.max_total_positions,
            "max_heat_pct": self.max_portfolio_heat_pct,
            "symbols": {
                sym: len(positions) for sym, positions in self._positions.items()
            },
        }
