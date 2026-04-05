"""
Stateful deterministic safety guards for the live trading loop.
Each class holds a rolling window and exposes a single decision method.

Guards:
  SignalHashFilter        — rejects duplicate setups within N cycles
  ConfidenceVarianceFilter — rejects unstable LLM confidence outputs
  SpreadRegimeFilter      — rejects spread spikes vs rolling median
  EquityCurveScaler       — graduated risk reduction when equity below 20-trade MA
  DrawdownPauseGuard      — per-symbol, magnitude-scaled pause on accelerating losses
  NearDedupGuard          — skip if open trade within N ATR of same symbol
  PortfolioCapGuard       — block when correlation-adjusted portfolio risk exceeds cap
"""

import hashlib
import logging
import math
import statistics
from collections import deque, defaultdict
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
    """
    Graduated risk reduction when equity trails its 20-trade moving average.

    Replaces the binary halve/full logic with a proportionate scale:
      < 3% below MA → 1.0x (no change)
      3–6% below MA → 0.75x (reduce 25%)
      6–10% below MA → 0.50x (halve)
      > 10% below MA → 0.25x (quarter size)
    """

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
        current = cumulative[-1]

        if moving_avg == 0 or current >= moving_avg:
            return 1.0

        deviation_pct = (moving_avg - current) / abs(moving_avg)

        if deviation_pct < 0.03:
            scale = 1.0
        elif deviation_pct < 0.06:
            scale = 0.75
        elif deviation_pct < 0.10:
            scale = 0.50
        else:
            scale = 0.25

        if scale < 1.0:
            logger.info(
                "[equity-curve] Equity %.2f is %.1f%% below MA %.2f — scale=%.2f",
                current, deviation_pct * 100, moving_avg, scale,
            )
        return scale


class DrawdownPauseGuard:
    """
    Per-symbol, magnitude-scaled pause on 3 consecutive accelerating losses.

    Pause duration scales with average R-multiple of the losses:
      avg_r < 1.5x  → 30 min
      1.5x–3.0x     → 90 min
      3.0x–5.0x     → 4 hours
      > 5.0x        → 24 hours

    Per-symbol scoping: XAUUSD losses only pause XAUUSD signals.
    Global pause (all symbols) only if losses span 2+ different symbols.
    """

    _PAUSE_TIERS = [
        (1.5, 30),
        (3.0, 90),
        (5.0, 240),
        (float("inf"), 1440),
    ]

    def __init__(self, pause_minutes: int = 30):
        self._default_pause_minutes = pause_minutes
        # Per-symbol recent trades: {symbol: deque[(pnl_usd, risk_usd)]}
        self._recent: dict[str, deque[tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=5)
        )
        # Per-symbol pause expiry: {symbol: datetime}
        self._paused_until: dict[str, datetime] = {}
        # Global pause (all symbols)
        self._global_pause_until: datetime | None = None

    def record_close(
        self,
        pnl_usd: float,
        risk_usd: float = 0.0,
        symbol: str = "ALL",
    ) -> None:
        """Record a trade close. risk_usd used for R-multiple scaling."""
        self._recent[symbol].append((pnl_usd, max(risk_usd, abs(pnl_usd) or 1.0)))

    def _compute_pause_minutes(self, last_3: list[tuple[float, float]]) -> int:
        """Scale pause duration by average loss R-multiple."""
        r_multiples = [
            abs(pnl) / risk if risk > 0 else 1.0
            for pnl, risk in last_3
        ]
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 1.0
        for threshold, minutes in self._PAUSE_TIERS:
            if avg_r < threshold:
                return minutes
        return self._default_pause_minutes

    def is_paused(self, symbol: str = "ALL") -> bool:
        """
        Check if a symbol (or globally) is paused.

        Args:
            symbol: Trading symbol to check (e.g. "XAUUSD"), or "ALL" for global.
        """
        now = datetime.now(timezone.utc)

        # Check global pause
        if self._global_pause_until and now < self._global_pause_until:
            return True
        if self._global_pause_until and now >= self._global_pause_until:
            self._global_pause_until = None

        # Check per-symbol pause
        if symbol != "ALL" and symbol in self._paused_until:
            if now < self._paused_until[symbol]:
                return True
            else:
                del self._paused_until[symbol]

        # Evaluate trigger on symbol history
        history = self._recent[symbol]
        if len(history) < 3:
            return False

        last_3 = list(history)[-3:]
        losses = [pnl for pnl, _ in last_3]

        if not all(p < 0 for p in losses):
            return False

        # Accelerating: each loss larger than previous
        if losses[1] < losses[0] and losses[2] < losses[1]:
            pause_min = self._compute_pause_minutes(last_3)
            expiry = now + timedelta(minutes=pause_min)

            # Check if other symbols are also losing — trigger global pause
            losing_symbols = [
                sym for sym, hist in self._recent.items()
                if sym != symbol and len(hist) >= 3
                and all(p < 0 for p, _ in list(hist)[-3:])
            ]
            if losing_symbols:
                self._global_pause_until = expiry
                logger.warning(
                    "[dd-pause] GLOBAL PAUSE %dmin — losses on %s and %s",
                    pause_min, symbol, losing_symbols,
                )
            else:
                self._paused_until[symbol] = expiry
                logger.warning(
                    "[dd-pause] %s PAUSED %dmin — 3 accelerating losses %s",
                    symbol, pause_min, [round(p, 2) for p in losses],
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
                owner = trade.get("instance_id", "unknown")
                logger.info(
                    "[near-dedup] SKIP — open trade at %.5g (owner=%s) is %.2f ATR away "
                    "(min=%.2f ATR)",
                    trade_entry, owner, atr_distance, self.min_atr_distance,
                )
                return True
        return False


class PortfolioCapGuard:
    """
    Block new trades when correlation-adjusted portfolio risk exceeds the cap.

    Replaces naive sum(risk_usd) with a simplified Markowitz variance calculation
    that accounts for correlated positions. Two gold trades in the same direction
    have ~1.0 correlation — their combined risk is nearly double the independent sum.

    Correlation matrix (static defaults, configurable per instance):
      Same symbol, same direction:  1.0 (identical positions)
      XAUUSD ↔ XAGUSD:              0.85
      XAUUSD ↔ XAUEUR:              0.90
      EURUSD ↔ GBPUSD:              0.75
      EURUSD ↔ USDJPY (same dir):  -0.65 (naturally hedged)
      Default (unspecified pairs):   0.30

    For opposite-direction trades, correlation is negated (positions partially hedge).
    """

    # Default pairwise correlation (absolute). Direction sign is applied at runtime.
    _DEFAULT_CORRELATIONS: dict[frozenset, float] = {
        frozenset({"XAUUSD", "XAGUSD"}): 0.85,
        frozenset({"XAUUSD", "XAUEUR"}): 0.90,
        frozenset({"EURUSD", "GBPUSD"}): 0.75,
        frozenset({"EURUSD", "USDJPY"}): 0.65,
        frozenset({"GBPUSD", "USDJPY"}): 0.55,
    }
    _DEFAULT_CORRELATION = 0.30   # for unspecified pairs

    def __init__(
        self,
        max_portfolio_risk_pct: float = 6.0,
        correlation_overrides: dict | None = None,
    ):
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        # Allow per-instance correlation overrides
        self._correlations = dict(self._DEFAULT_CORRELATIONS)
        if correlation_overrides:
            for pair_str, rho in correlation_overrides.items():
                symbols = [s.strip() for s in pair_str.split(",")]
                if len(symbols) == 2:
                    self._correlations[frozenset(symbols)] = float(rho)

    def _get_correlation(self, sym_a: str, sym_b: str, dir_a: str, dir_b: str) -> float:
        """Get signed correlation between two trades."""
        # Phase 7K: Same symbol → perfect correlation (identical exposure)
        if sym_a == sym_b:
            abs_rho = 1.0
        else:
            abs_rho = self._correlations.get(
                frozenset({sym_a, sym_b}), self._DEFAULT_CORRELATION
            )
        # Same direction → positive correlation; opposite → negative
        sign = 1.0 if dir_a == dir_b else -1.0
        return abs_rho * sign

    def _correlation_adjusted_risk(
        self, risks: list[float], symbols: list[str], directions: list[str]
    ) -> float:
        """
        Compute portfolio variance via simplified Markowitz formula.

        portfolio_variance = Σ(ri²) + 2 * Σ(rho_ij * ri * rj)
        portfolio_risk = sqrt(portfolio_variance)
        """
        n = len(risks)
        if n == 0:
            return 0.0
        if n == 1:
            return risks[0]

        variance = sum(r ** 2 for r in risks)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._get_correlation(
                    symbols[i], symbols[j], directions[i], directions[j]
                )
                variance += 2 * rho * risks[i] * risks[j]

        return math.sqrt(max(variance, 0.0))

    def is_capped(
        self,
        open_trades: list[dict],
        balance: float,
    ) -> bool:
        """
        Check if correlation-adjusted open risk exceeds portfolio cap.

        Args:
            open_trades: List of open trade dicts with 'risk_amount_usd',
                         'symbol', and 'direction' fields.
            balance: Current account balance.

        Returns:
            True if portfolio risk exceeds cap.
        """
        if balance <= 0:
            return True

        risks: list[float] = []
        symbols: list[str] = []
        directions: list[str] = []

        for trade in open_trades:
            risk = float(trade.get("risk_amount_usd") or 0)
            if risk <= 0:
                risk_pct = float(trade.get("risk_pct") or 0.01)
                risk = balance * risk_pct
            risks.append(risk)
            symbols.append(str(trade.get("symbol") or "UNKNOWN"))
            directions.append(str(trade.get("direction") or "BUY").upper())

        # Correlation-adjusted portfolio risk
        adj_risk_usd = self._correlation_adjusted_risk(risks, symbols, directions)
        simple_risk_usd = sum(risks)
        portfolio_risk_pct = (adj_risk_usd / balance) * 100

        if portfolio_risk_pct >= self.max_portfolio_risk_pct:
            logger.info(
                "[portfolio-cap] BLOCKED — corr-adj risk %.1f%% >= cap %.1f%% "
                "($%.0f corr-adj, $%.0f simple, %d trades on $%.0f balance)",
                portfolio_risk_pct, self.max_portfolio_risk_pct,
                adj_risk_usd, simple_risk_usd, len(open_trades), balance,
            )
            return True

        logger.debug(
            "[portfolio-cap] OK — corr-adj=%.1f%% simple=%.1f%% cap=%.1f%%",
            portfolio_risk_pct, (simple_risk_usd / balance) * 100,
            self.max_portfolio_risk_pct,
        )
        return False
