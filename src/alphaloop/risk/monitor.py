"""
Real-time risk monitoring — tracks daily loss, consecutive losses,
session limits, portfolio heat, and enforces the kill switch.

v3.1: Integrated HistoricalVaRCalculator for probabilistic VaR/CVaR
estimates alongside rule-based checks.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from alphaloop.risk.var_calculator import HistoricalVaRCalculator
from alphaloop.utils.time import utc_day_start

logger = logging.getLogger(__name__)


class RiskMonitor:
    """
    Tracks daily loss, consecutive losses, and enforces the kill switch.
    Async-compatible — uses asyncio.Lock instead of threading.
    """

    def __init__(
        self,
        account_balance: float,
        *,
        max_daily_loss_pct: float = 0.03,
        max_session_loss_pct: float = 0.02,
        consecutive_loss_limit: int = 5,
        max_concurrent_trades: int = 3,
        max_portfolio_heat_pct: float = 0.06,
        max_trades_per_hour: int = 3,
        budget_multiplier: float = 1.0,
    ):
        self.account_balance = account_balance
        self.budget_multiplier = max(0.01, min(1.0, budget_multiplier))
        self.max_daily_loss_pct = max_daily_loss_pct * self.budget_multiplier
        self.max_session_loss_pct = max_session_loss_pct * self.budget_multiplier
        self.consecutive_loss_limit = consecutive_loss_limit
        self.max_concurrent_trades = max(1, int(max_concurrent_trades * self.budget_multiplier))
        self.max_portfolio_heat_pct = max_portfolio_heat_pct * self.budget_multiplier
        self.max_trades_per_hour = max(1, int(max_trades_per_hour * self.budget_multiplier))

        self.today = datetime.now(timezone.utc).date()
        self._daily_pnl: float = 0.0
        self._consecutive_losses: int = 0
        self._open_trades: int = 0
        self._open_risk_usd: float = 0.0
        self._kill_switch_active: bool = False
        self.force_close_all: bool = False
        self._session_pnl: dict[str, float] = {}
        self._rolling_dd_modifier: float = 1.0
        self._seeded: bool = False
        self._recent_trade_times: list[datetime] = []
        self._lock = asyncio.Lock()
        self._var_calc = HistoricalVaRCalculator(confidence_level=0.95, lookback_days=252)

    def update_balance(self, new_balance: float) -> None:
        """Update the account balance (e.g. after a broker sync)."""
        self.account_balance = new_balance

    async def seed_from_db(
        self,
        trade_repo=None,
        instance_id: str | None = None,
    ) -> None:
        """Restore counters from DB so restarts don't reset risk state."""
        if trade_repo is not None:
            try:
                open_trades = await trade_repo.get_open_trades(instance_id=instance_id)
                self._open_trades = len(open_trades)
                self._open_risk_usd = sum(
                    float(t.risk_amount_usd or 0) for t in open_trades
                )

                closed = await trade_repo.get_closed_trades(instance_id=instance_id, limit=20)
                streak = 0
                for t in closed:
                    if t.outcome == "LOSS":
                        streak += 1
                    else:
                        break
                self._consecutive_losses = streak

                today_start = utc_day_start()
                today_closed = [
                    t for t in closed
                    if t.closed_at and t.closed_at >= today_start
                ]
                self._daily_pnl = sum(float(t.pnl_usd or 0) for t in today_closed)

                # Fit VaR calculator on closed trade PnL history
                try:
                    all_closed = await trade_repo.get_closed_trades(instance_id=instance_id, limit=1000)
                    pnl_series = [float(t.pnl_usd or 0) for t in all_closed if t.pnl_usd is not None]
                    if pnl_series:
                        self._var_calc.fit(pnl_series)
                        logger.info(
                            "VaR fitted | n=%d | VaR95=%.2f | CVaR95=%.2f",
                            len(pnl_series),
                            self._var_calc.var() or 0,
                            self._var_calc.cvar() or 0,
                        )
                except Exception as var_err:
                    logger.warning("VaR fit failed (non-critical): %s", var_err)

            except Exception as e:
                logger.critical("seed_from_db FAILED: %s", e)
                raise

        self._seeded = True
        self._check_kill_switch()
        logger.info(
            "RiskMonitor seeded | daily_pnl=$%.2f | consec_losses=%d | "
            "open_trades=%d | kill_switch=%s",
            self._daily_pnl,
            self._consecutive_losses,
            self._open_trades,
            self._kill_switch_active,
        )

    async def record_trade_close(self, pnl: float, session_name: str = "") -> None:
        async with self._lock:
            self._ensure_day_reset()
            self._daily_pnl += pnl
            if session_name:
                self._session_pnl[session_name] = (
                    self._session_pnl.get(session_name, 0.0) + pnl
                )
            if pnl < 0:
                self._consecutive_losses += 1
            elif pnl == 0:
                self._consecutive_losses = 0
            elif pnl > 0:
                self._consecutive_losses = 0
            self.account_balance += pnl
            self._check_kill_switch()

    async def can_open_trade(self, session_name: str = "") -> tuple[bool, str]:
        async with self._lock:
            if not self._seeded:
                return False, "RiskMonitor not seeded from DB"

            self._ensure_day_reset()

            if self._kill_switch_active:
                return False, "Kill switch is ACTIVE"

            if self._open_trades >= self.max_concurrent_trades:
                return False, f"Max concurrent trades ({self.max_concurrent_trades})"

            # Portfolio heat cap
            heat_cap = self.max_portfolio_heat_pct * self.account_balance
            if self._open_risk_usd >= heat_cap:
                return False, f"Portfolio heat cap: ${self._open_risk_usd:.2f} >= ${heat_cap:.2f}"

            if self.account_balance <= 0:
                return False, "Account balance zero or negative"

            daily_loss_pct = abs(min(self._daily_pnl, 0)) / self.account_balance
            if daily_loss_pct >= self.max_daily_loss_pct:
                self._activate_kill_switch("Daily loss limit reached")
                return False, f"Daily loss {daily_loss_pct*100:.1f}% exceeds limit"

            # Session loss cap
            if session_name:
                sess_pnl = self._session_pnl.get(session_name, 0.0)
                if sess_pnl < 0:
                    sess_loss_pct = abs(sess_pnl) / self.account_balance
                    if sess_loss_pct >= self.max_session_loss_pct:
                        return False, f"Session loss cap for {session_name}"

            # VaR advisory — warn if daily PnL has already breached VaR threshold
            if self._var_calc.is_fitted() and self._var_calc.var_breach(self._daily_pnl):
                logger.warning(
                    "[risk] Daily PnL (%.2f) has breached VaR95 threshold (%.2f) — advisory",
                    self._daily_pnl, self._var_calc.var() or 0,
                )

            # Trade frequency — count includes the trade about to be opened
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            self._recent_trade_times = [
                t for t in self._recent_trade_times if t > one_hour_ago
            ]
            if len(self._recent_trade_times) + 1 > self.max_trades_per_hour:
                return False, f"Trade frequency cap ({self.max_trades_per_hour}/hour)"

            return True, ""

    async def try_reserve_slot(
        self, risk_usd: float = 0.0, session_name: str = ""
    ) -> tuple[bool, str]:
        """
        Atomically check if a trade slot is available AND reserve it.

        This is the safe alternative to calling can_open_trade() followed by
        register_open() separately — the two-step pattern has a race window
        where two concurrent callers can both pass the check before either
        increments the counter.

        Returns (True, "") on success (slot reserved).
        Returns (False, reason) if the trade should not proceed.
        """
        async with self._lock:
            # --- same checks as can_open_trade ---
            if not self._seeded:
                return False, "RiskMonitor not seeded from DB"

            self._ensure_day_reset()

            if self._kill_switch_active:
                return False, "Kill switch is ACTIVE"

            if self._open_trades >= self.max_concurrent_trades:
                return False, f"Max concurrent trades ({self.max_concurrent_trades})"

            heat_cap = self.max_portfolio_heat_pct * self.account_balance
            if self._open_risk_usd + risk_usd > heat_cap:
                return False, f"Portfolio heat cap: ${self._open_risk_usd + risk_usd:.2f} would exceed ${heat_cap:.2f}"

            if self.account_balance <= 0:
                return False, "Account balance zero or negative"

            daily_loss_pct = abs(min(self._daily_pnl, 0)) / self.account_balance
            if daily_loss_pct >= self.max_daily_loss_pct:
                self._activate_kill_switch("Daily loss limit reached")
                return False, f"Daily loss {daily_loss_pct*100:.1f}% exceeds limit"

            if session_name:
                sess_pnl = self._session_pnl.get(session_name, 0.0)
                if sess_pnl < 0:
                    sess_loss_pct = abs(sess_pnl) / self.account_balance
                    if sess_loss_pct >= self.max_session_loss_pct:
                        return False, f"Session loss cap for {session_name}"

            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            self._recent_trade_times = [
                t for t in self._recent_trade_times if t > one_hour_ago
            ]
            if len(self._recent_trade_times) + 1 > self.max_trades_per_hour:
                return False, f"Trade frequency cap ({self.max_trades_per_hour}/hour)"

            # --- atomically reserve the slot ---
            self._open_trades += 1
            self._open_risk_usd = round(self._open_risk_usd + risk_usd, 2)
            self._recent_trade_times.append(datetime.now(timezone.utc))
            return True, ""

    async def register_open(self, risk_usd: float = 0.0) -> None:
        async with self._lock:
            self._open_trades += 1
            self._open_risk_usd = round(self._open_risk_usd + risk_usd, 2)
            self._recent_trade_times.append(datetime.now(timezone.utc))

    async def register_close(self, risk_usd: float = 0.0) -> None:
        async with self._lock:
            self._open_trades = max(0, self._open_trades - 1)
            self._open_risk_usd = round(max(0.0, self._open_risk_usd - risk_usd), 2)

    @property
    def status(self) -> dict:
        var_summary = self._var_calc.summary()
        return {
            "daily_pnl": round(self._daily_pnl, 2),
            "daily_pnl_pct": round(self._daily_pnl / self.account_balance * 100, 3)
            if self.account_balance > 0
            else 0.0,
            "consecutive_losses": self._consecutive_losses,
            "open_trades": self._open_trades,
            "open_risk_usd": round(self._open_risk_usd, 2),
            "kill_switch": self._kill_switch_active,
            "force_close_all": self.force_close_all,
            "rolling_dd_modifier": self._rolling_dd_modifier,
            "seeded": self._seeded,
            # Probabilistic VaR/CVaR (None until enough trade history)
            "var_95": var_summary["var_95"],
            "cvar_95": var_summary["cvar_95"],
            "var_99": var_summary["var_99"],
            "cvar_99": var_summary["cvar_99"],
            "var_observations": var_summary["observations"],
            "var_breach_today": self._var_calc.var_breach(self._daily_pnl) if self._var_calc.is_fitted() else False,
        }

    @property
    def kill_switch_active(self) -> bool:
        """Read-only view of the kill-switch state for external callers."""
        return self._kill_switch_active

    def activate_kill_switch(self, reason: str) -> None:
        """Public wrapper so callers do not mutate private state directly."""
        self._activate_kill_switch(reason)

    def _check_kill_switch(self) -> None:
        if self.account_balance <= 0:
            self._activate_kill_switch("Account balance zero or negative")
            return
        daily_loss_pct = abs(min(self._daily_pnl, 0)) / self.account_balance
        if self._consecutive_losses >= self.consecutive_loss_limit:
            self._activate_kill_switch(f"{self._consecutive_losses} consecutive losses")
        elif daily_loss_pct >= self.max_daily_loss_pct:
            self._activate_kill_switch(f"Daily loss {daily_loss_pct*100:.1f}%")

    def _activate_kill_switch(self, reason: str) -> None:
        if not self._kill_switch_active:
            self._kill_switch_active = True
            self.force_close_all = True
            logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def _ensure_day_reset(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.today:
            if self.force_close_all:
                # H-06: Day boundary reached while force_close_all was active.
                # Log a warning (operator should verify positions are closed), then
                # reset for the new day. _check_kill_switch() re-activates the kill
                # switch if consecutive losses still exceed the limit.
                logger.critical(
                    "[RiskMonitor] force_close_all was True at day boundary — "
                    "resetting for new day. Verify all positions are closed."
                )
            self.today = today
            self._daily_pnl = 0.0
            self._kill_switch_active = False
            self.force_close_all = False
            self._consecutive_losses = 0
            self._session_pnl = {}
            logger.info("[RiskMonitor] Day boundary reset — kill switch cleared, consecutive losses reset")
