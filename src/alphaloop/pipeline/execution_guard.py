"""
pipeline/execution_guard.py — Stage 8: Execution safety.

Last-mile checks before order submission.  Can EXECUTE, DELAY, or BLOCK.

Delay-eligible guards (transient conditions):
  - spread spike  → wait 1-3 candles for normalisation
  - tick jump     → wait 1 candle for spike absorption
  - liq vacuum    → wait 1 candle for fill

Block-only guards (permanent conditions):
  - signal hash dedup     → same setup already attempted
  - near position dedup   → open trade within 1 ATR
  - confidence variance   → unstable AI output
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from alphaloop.pipeline.types import CandidateSignal, ExecutionGuardResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Delay queue
# ---------------------------------------------------------------------------

@dataclass
class DelayedSignal:
    """A signal waiting for transient execution conditions to clear."""

    signal: CandidateSignal
    reason: str
    max_delay_candles: int
    candles_waited: int = 0
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ExecutionGuardRunner:
    """
    Runs all execution safety checks.

    Transient failures (spread spike, tick jump, liq vacuum) produce DELAY
    instead of BLOCK.  Permanent failures always BLOCK.

    Maintains a per-symbol delay queue (max 1 delayed signal per symbol).
    """

    def __init__(
        self,
        *,
        # Guard instances from risk/guards.py
        signal_hash_filter=None,
        confidence_variance_filter=None,
        spread_regime_filter=None,
        near_dedup_guard=None,
        # Plugin tool instances (wired from strategy card toggles)
        tick_jump_tool=None,
        liq_vacuum_tool=None,
        # Thresholds (fallback when plugins are None)
        tick_jump_atr_max: float = 0.8,
        liq_vacuum_spike_mult: float = 2.5,
        liq_vacuum_body_pct: float = 30.0,
        max_delay_candles: int = 3,
    ):
        self._hash_filter = signal_hash_filter
        self._conf_var_filter = confidence_variance_filter
        self._spread_filter = spread_regime_filter
        self._near_dedup = near_dedup_guard

        self._tick_jump_tool = tick_jump_tool    # TickJumpGuard plugin
        self._liq_vacuum_tool = liq_vacuum_tool  # LiqVacuumGuard plugin

        self._tick_jump_max = tick_jump_atr_max
        self._liq_spike_mult = liq_vacuum_spike_mult
        self._liq_body_pct = liq_vacuum_body_pct
        self._max_delay = max_delay_candles

        # Per-symbol delay queue
        self._delayed: dict[str, DelayedSignal] = {}

    async def check(
        self,
        signal: CandidateSignal,
        context,
        *,
        symbol: str = "",
        enabled_tools: dict[str, bool] | None = None,
    ) -> ExecutionGuardResult:
        """Run all execution guards.  Returns EXECUTE, DELAY, or BLOCK.

        When *enabled_tools* is provided, guards whose corresponding
        strategy tool is toggled OFF are skipped.
        """

        tools = enabled_tools or {}
        warnings: list[str] = []
        indicators = getattr(context, "indicators", {})
        m15 = indicators.get("M15", {})

        # --- Block-only guards (permanent) ---

        # Signal hash dedup
        if self._hash_filter:
            ema200_state = indicators.get("H1", {}).get("trend_bias", "unknown")
            tf_ctx = {"timeframes": {"H1": {"indicators": indicators.get("H1", {})}}}
            if self._hash_filter.is_duplicate(symbol, signal, tf_ctx):
                return ExecutionGuardResult(
                    action="BLOCK",
                    block_reason="Duplicate signal hash",
                    blocked_by="signal_hash_dedup",
                )

        # Near position dedup
        if self._near_dedup:
            open_trades = getattr(context, "open_trades", [])
            atr = float(m15.get("atr", 0) or 0)
            entry_mid = (signal.entry_zone[0] + signal.entry_zone[1]) / 2
            # Phase 7H: fix arg order — was (symbol, entry, atr, trades)
            # Real signature: is_too_close(proposed_entry, atr, open_trades, symbol)
            if self._near_dedup.is_too_close(
                proposed_entry=entry_mid, atr=atr,
                open_trades=open_trades, symbol=symbol,
            ):
                return ExecutionGuardResult(
                    action="BLOCK",
                    block_reason="Open position within 1 ATR",
                    blocked_by="near_position_dedup",
                )

        # Confidence variance
        if self._conf_var_filter:
            self._conf_var_filter.record(signal.raw_confidence)
            if self._conf_var_filter.is_unstable():
                return ExecutionGuardResult(
                    action="BLOCK",
                    block_reason="Confidence variance too high",
                    blocked_by="confidence_variance",
                )

        # --- Delay-eligible guards (transient) ---

        # Spread spike
        if self._spread_filter:
            price = getattr(context, "price", None)
            if price:
                spread = float(getattr(price, "spread", 0) or 0)
                if self._spread_filter.is_spike(spread):
                    return ExecutionGuardResult(
                        action="DELAY",
                        delay_candles=min(3, self._max_delay),
                        delay_reason=f"Spread spike detected ({spread})",
                    )

        # Tick jump — use plugin if wired, else fall back to indicator read
        if self._tick_jump_tool:
            try:
                tj_result = await self._tick_jump_tool.timed_run(context)
                if not tj_result.passed:
                    return ExecutionGuardResult(
                        action="DELAY",
                        delay_candles=1,
                        delay_reason=tj_result.reason,
                    )
            except Exception as exc:
                logger.warning("[ExecGuard] tick_jump_tool error: %s", exc)
        elif tools.get("tick_jump_guard", True):
            # Fallback: read pre-computed indicator value
            tick_jump = m15.get("tick_jump_atr")
            if tick_jump is not None and float(tick_jump) > self._tick_jump_max:
                return ExecutionGuardResult(
                    action="DELAY",
                    delay_candles=1,
                    delay_reason=(
                        f"Tick jump {float(tick_jump):.2f} ATR "
                        f"> {self._tick_jump_max} threshold"
                    ),
                )

        # Liquidity vacuum — use plugin if wired, else fall back to indicator read
        if self._liq_vacuum_tool:
            try:
                lv_result = await self._liq_vacuum_tool.timed_run(context)
                if not lv_result.passed:
                    return ExecutionGuardResult(
                        action="DELAY",
                        delay_candles=1,
                        delay_reason=lv_result.reason,
                    )
            except Exception as exc:
                logger.warning("[ExecGuard] liq_vacuum_tool error: %s", exc)
        elif tools.get("liq_vacuum_guard", True):
            # Fallback: read pre-computed indicator value
            liq = m15.get("liq_vacuum", {})
            if liq:
                range_atr = float(liq.get("bar_range_atr", 0) or 0)
                body_pct = float(liq.get("body_pct", 100) or 100)
                if range_atr > self._liq_spike_mult and body_pct < self._liq_body_pct:
                    return ExecutionGuardResult(
                        action="DELAY",
                        delay_candles=1,
                        delay_reason=(
                            f"Liquidity vacuum: range={range_atr:.1f}x ATR, "
                            f"body={body_pct:.0f}%"
                        ),
                    )

        return ExecutionGuardResult(action="EXECUTE")

    # ------------------------------------------------------------------
    # Delay queue management
    # ------------------------------------------------------------------

    def queue_delay(self, symbol: str, signal: CandidateSignal, reason: str) -> None:
        """Store a delayed signal for re-evaluation next cycle."""
        self._delayed[symbol] = DelayedSignal(
            signal=signal,
            reason=reason,
            max_delay_candles=self._max_delay,
        )
        logger.info(
            "[ExecGuard] DELAY queued: %s %s — %s (max %d candles)",
            symbol,
            signal.direction,
            reason,
            self._max_delay,
        )

    def get_delayed(self, symbol: str) -> DelayedSignal | None:
        """Get a pending delayed signal for a symbol."""
        return self._delayed.get(symbol)

    def tick_delay(self, symbol: str) -> DelayedSignal | None:
        """
        Increment the candle counter for a delayed signal.
        Returns the signal if still valid, None if expired.
        """
        ds = self._delayed.get(symbol)
        if ds is None:
            return None

        ds.candles_waited += 1
        if ds.candles_waited > ds.max_delay_candles:
            logger.info(
                "[ExecGuard] DELAY expired: %s %s after %d candles",
                symbol,
                ds.signal.direction,
                ds.candles_waited,
            )
            del self._delayed[symbol]
            return None

        return ds

    def clear_delay(self, symbol: str) -> None:
        """Remove a delayed signal (executed or superseded)."""
        self._delayed.pop(symbol, None)
