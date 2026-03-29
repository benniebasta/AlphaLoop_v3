"""
Stateful deterministic safety guards for the live trading loop.
Each class holds a rolling window and exposes a single decision method.

Guards:
  SignalHashFilter        — rejects duplicate setups within N cycles
  ConfidenceVarianceFilter — rejects unstable LLM confidence outputs
  SpreadRegimeFilter      — rejects spread spikes vs rolling median
  EquityCurveScaler       — halves risk when equity below 20-trade MA
  DrawdownPauseGuard      — pauses trading when DD slope is accelerating
  NearDedupGuard          — skip if open trade within N ATR of same symbol
  PortfolioCapGuard       — block when total open risk exceeds portfolio cap
"""

import hashlib
import logging
import statistics
from collections import deque
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class SignalHashFilter:
    """Rejects duplicate setups within the last N cycles."""

    def __init__(self, window: int = 3):
        self.window = window
        self._hashes: deque[str] = deque(maxlen=window)

    @staticmethod
    def _make_hash(symbol: str, signal, ema200_state: str) -> str:
        zone_lo = round(signal.entry_zone[0], 1)
        zone_hi = round(signal.entry_zone[1], 1)
        raw = f"{symbol}|{signal.direction}|{zone_lo}-{zone_hi}|{ema200_state}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def is_duplicate(self, symbol: str, signal, context: dict) -> bool:
        h1_ind = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
        ema200_state = h1_ind.get("trend_bias", "unknown")
        sig_hash = self._make_hash(symbol, signal, ema200_state)
        if sig_hash in self._hashes:
            logger.info("[hash-filter] Duplicate signal %s — skipping", sig_hash)
            return True
        self._hashes.append(sig_hash)
        return False


class ConfidenceVarianceFilter:
    """Rejects when last N confidence scores have high variance."""

    def __init__(self, window: int = 3, max_stdev: float = 0.15):
        self.window = window
        self.max_stdev = max_stdev
        self._confs: deque[float] = deque(maxlen=window)

    def record(self, confidence: float) -> None:
        self._confs.append(confidence)

    def is_unstable(self) -> bool:
        if len(self._confs) < self.window:
            return False
        try:
            sd = statistics.stdev(self._confs)
        except statistics.StatisticsError:
            return False
        if sd > self.max_stdev:
            logger.info(
                "[conf-variance] SKIP — stdev %.3f > %.3f over %s",
                sd, self.max_stdev, [round(c, 2) for c in self._confs],
            )
            return True
        return False


class SpreadRegimeFilter:
    """Rejects if current spread > median * threshold."""

    def __init__(self, window: int = 50, threshold: float = 1.8):
        self.threshold = threshold
        self._spreads: deque[float] = deque(maxlen=window)

    def record(self, spread: float) -> None:
        if spread > 0:
            self._spreads.append(spread)

    def is_spike(self, spread: float) -> bool:
        if len(self._spreads) < 10:
            return False
        median = statistics.median(self._spreads)
        if median <= 0:
            return False
        ratio = spread / median
        if ratio > self.threshold:
            logger.info(
                "[spread-regime] SKIP — spread %.1f = %.2fx median %.1f",
                spread, ratio, median,
            )
            return True
        return False


class EquityCurveScaler:
    """Halves risk when equity is below its 20-trade moving average."""

    def __init__(self, window: int = 20):
        self.window = window
        self._pnl: deque[float] = deque(maxlen=window)

    def record_pnl(self, pnl_usd: float) -> None:
        self._pnl.append(pnl_usd)

    def risk_scale(self) -> float:
        if len(self._pnl) < self.window:
            return 1.0
        cumulative = []
        running = 0.0
        for p in self._pnl:
            running += p
            cumulative.append(running)
        moving_avg = sum(cumulative) / len(cumulative)
        if cumulative[-1] < moving_avg:
            logger.info(
                "[equity-curve] Equity %.2f below MA %.2f — halving risk",
                cumulative[-1], moving_avg,
            )
            return 0.5
        return 1.0


class DrawdownPauseGuard:
    """Pauses entries when 3 consecutive losses with accelerating magnitude."""

    def __init__(self, pause_minutes: int = 30):
        self.pause_minutes = pause_minutes
        self._recent_pnl: deque[float] = deque(maxlen=5)
        self._paused_until: datetime | None = None

    def record_close(self, pnl_usd: float) -> None:
        self._recent_pnl.append(pnl_usd)

    def is_paused(self) -> bool:
        now = datetime.now(timezone.utc)
        if self._paused_until and now < self._paused_until:
            return True
        self._paused_until = None

        if len(self._recent_pnl) < 3:
            return False
        last_3 = list(self._recent_pnl)[-3:]
        if not all(p < 0 for p in last_3):
            return False
        if last_3[1] < last_3[0] and last_3[2] < last_3[1]:
            self._paused_until = now + timedelta(minutes=self.pause_minutes)
            logger.warning(
                "[dd-pause] ACTIVATED — 3 losses with accelerating DD %s",
                [round(p, 2) for p in last_3],
            )
            return True
        return False


class NearDedupGuard:
    """
    Skip new trades if an open trade on the same symbol is within N ATR
    of the proposed entry. Prevents stacking nearly identical positions.
    """

    def __init__(self, min_atr_distance: float = 1.0):
        self.min_atr_distance = min_atr_distance

    def is_too_close(
        self,
        proposed_entry: float,
        atr: float,
        open_trades: list[dict],
        symbol: str,
    ) -> bool:
        """
        Check if any open trade on the same symbol is within min_atr_distance.

        Args:
            proposed_entry: The entry price of the new signal.
            atr: Current ATR value.
            open_trades: List of open trade dicts with 'symbol' and 'entry_price'.
            symbol: The symbol to check.

        Returns:
            True if too close to an existing open trade.
        """
        if atr <= 0:
            return False
        for trade in open_trades:
            if trade.get("symbol") != symbol:
                continue
            trade_entry = trade.get("entry_price", 0)
            distance = abs(proposed_entry - trade_entry)
            atr_distance = distance / atr
            if atr_distance < self.min_atr_distance:
                logger.info(
                    "[near-dedup] SKIP — open trade at %.5g is %.2f ATR away "
                    "(min=%.2f ATR)",
                    trade_entry, atr_distance, self.min_atr_distance,
                )
                return True
        return False


class PortfolioCapGuard:
    """
    Block new trades when total open risk across all positions exceeds
    a portfolio-level cap (risk_pct * max_concurrent).

    This prevents portfolio heat from accumulating beyond safe levels.
    """

    def __init__(
        self,
        max_portfolio_risk_pct: float = 6.0,
    ):
        self.max_portfolio_risk_pct = max_portfolio_risk_pct

    def is_capped(
        self,
        open_trades: list[dict],
        balance: float,
    ) -> bool:
        """
        Check if total open risk exceeds portfolio cap.

        Args:
            open_trades: List of open trade dicts with 'risk_amount_usd' or
                         'risk_pct' fields.
            balance: Current account balance.

        Returns:
            True if portfolio risk exceeds cap.
        """
        if balance <= 0:
            return True

        total_risk_usd = 0.0
        for trade in open_trades:
            risk = trade.get("risk_amount_usd", 0)
            if risk <= 0:
                # Fallback: estimate from risk_pct
                risk_pct = trade.get("risk_pct", 0.01)
                risk = balance * risk_pct
            total_risk_usd += risk

        portfolio_risk_pct = (total_risk_usd / balance) * 100
        if portfolio_risk_pct >= self.max_portfolio_risk_pct:
            logger.info(
                "[portfolio-cap] BLOCKED — open risk %.1f%% >= cap %.1f%% "
                "(%d trades, $%.0f risk on $%.0f balance)",
                portfolio_risk_pct, self.max_portfolio_risk_pct,
                len(open_trades), total_risk_usd, balance,
            )
            return True
        return False
