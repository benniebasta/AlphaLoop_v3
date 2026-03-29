"""
Real-time risk monitoring — tracks daily loss, consecutive losses,
session limits, portfolio heat, and enforces the kill switch.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

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
    ):
        self.account_balance = account_balance
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_session_loss_pct = max_session_loss_pct
        self.consecutive_loss_limit = consecutive_loss_limit
        self.max_concurrent_trades = max_concurrent_trades
        self.max_portfolio_heat_pct = max_portfolio_heat_pct
        self.max_trades_per_hour = max_trades_per_hour

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

                today_start = datetime.combine(
                    date.today(), datetime.min.time()
                ).replace(tzinfo=timezone.utc)
                today_closed = [
                    t for t in closed
                    if t.closed_at and t.closed_at >= today_start
                ]
                self._daily_pnl = sum(float(t.pnl_usd or 0) for t in today_closed)
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

            # Trade frequency
            one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            self._recent_trade_times = [
                t for t in self._recent_trade_times if t > one_hour_ago
            ]
            if len(self._recent_trade_times) >= self.max_trades_per_hour:
                return False, f"Trade frequency cap ({self.max_trades_per_hour}/hour)"

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
        }

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
                logger.critical(
                    "force_close_all still True at day boundary — "
                    "kill switch stays ACTIVE"
                )
                self.today = today
                self._daily_pnl = 0.0
                self._session_pnl = {}
                return
            self.today = today
            self._daily_pnl = 0.0
            self._kill_switch_active = False
            self.force_close_all = False
            self._session_pnl = {}
