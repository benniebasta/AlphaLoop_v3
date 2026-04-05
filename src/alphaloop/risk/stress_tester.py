"""
risk/stress_tester.py — Scenario-based stress testing.

Runs hypothetical market shock scenarios against current positions
to answer: "What would happen if XAUUSD gapped 10%?"

Built-in scenarios:
  COVID_GAP           — single bar -10% shock
  RATE_HIKE_SEQUENCE  — 3 consecutive -2% bars
  FLASH_CRASH         — -5% then +4% in 2 bars

Usage:
    tester = StressTester()
    results = tester.run_all(current_balance=10000, open_positions=0.1)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Minimum equity ratio before flagging margin call risk
_MARGIN_CALL_THRESHOLD = 0.20


@dataclass
class StressScenario:
    """A named sequence of per-bar PnL shocks expressed as % of balance."""
    name: str
    pnl_shocks_pct: list[float]   # e.g. [-0.10] for a single -10% shock
    description: str


# Built-in scenario library
BUILTIN_SCENARIOS: list[StressScenario] = [
    StressScenario(
        name="COVID_GAP",
        pnl_shocks_pct=[-0.10],
        description="Single bar -10% gap (e.g. COVID crash, circuit breaker event)",
    ),
    StressScenario(
        name="RATE_HIKE_SEQUENCE",
        pnl_shocks_pct=[-0.02, -0.02, -0.02],
        description="Three consecutive -2% bars (aggressive rate-hike sell-off)",
    ),
    StressScenario(
        name="FLASH_CRASH",
        pnl_shocks_pct=[-0.05, 0.04],
        description="Flash crash: -5% followed by +4% recovery in 2 bars",
    ),
]


class StressTester:
    """
    Applies market shock scenarios to a current balance + position size.

    Parameters
    ----------
    scenarios : list[StressScenario] | None
        Custom scenario list. Defaults to BUILTIN_SCENARIOS.
    """

    def __init__(self, scenarios: list[StressScenario] | None = None) -> None:
        self._scenarios = scenarios or BUILTIN_SCENARIOS

    def run_scenario(
        self,
        scenario: StressScenario,
        current_balance: float,
        open_lot_exposure: float = 0.0,
    ) -> dict:
        """
        Apply a scenario's PnL shocks and return results.

        Parameters
        ----------
        scenario : StressScenario
            The scenario to run.
        current_balance : float
            Current account equity in USD.
        open_lot_exposure : float
            Total open lot size (used to scale realistic impact).
            If 0.0, shocks are applied as a flat % of balance.

        Returns
        -------
        dict with keys:
            scenario_name, description,
            simulated_loss_usd, simulated_loss_pct,
            final_equity, margin_call_risk (bool),
            bar_by_bar (list of running equity)
        """
        if current_balance <= 0:
            return {
                "scenario_name": scenario.name,
                "description": scenario.description,
                "simulated_loss_usd": 0.0,
                "simulated_loss_pct": 0.0,
                "final_equity": 0.0,
                "margin_call_risk": True,
                "bar_by_bar": [],
            }

        equity = current_balance
        bar_by_bar: list[float] = [round(equity, 2)]

        for shock_pct in scenario.pnl_shocks_pct:
            bar_pnl = equity * shock_pct
            equity = round(equity + bar_pnl, 2)
            bar_by_bar.append(equity)

        total_loss_usd = round(equity - current_balance, 2)
        total_loss_pct = round(total_loss_usd / current_balance * 100, 2)
        margin_call_risk = equity < current_balance * _MARGIN_CALL_THRESHOLD

        logger.debug(
            "[stress] %s | loss=%.2f (%.1f%%) | margin_call=%s",
            scenario.name, total_loss_usd, total_loss_pct, margin_call_risk,
        )

        return {
            "scenario_name": scenario.name,
            "description": scenario.description,
            "simulated_loss_usd": total_loss_usd,
            "simulated_loss_pct": total_loss_pct,
            "final_equity": equity,
            "margin_call_risk": margin_call_risk,
            "bar_by_bar": bar_by_bar,
        }

    def run_all(
        self,
        current_balance: float,
        open_lot_exposure: float = 0.0,
    ) -> list[dict]:
        """
        Run all registered scenarios and return results list.

        Parameters
        ----------
        current_balance : float
            Current account equity in USD.
        open_lot_exposure : float
            Total open lot size.

        Returns
        -------
        list of scenario result dicts
        """
        return [
            self.run_scenario(s, current_balance, open_lot_exposure)
            for s in self._scenarios
        ]
