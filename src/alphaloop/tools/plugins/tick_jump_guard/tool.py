"""
Tick jump guard — detects abnormal 2-bar price spikes.

Blocks entries when the 2-bar price move (|close[-1] - close[-3]|) exceeds
0.8x ATR, which indicates a sudden spike that may retrace or widen spreads.

Does not block if the value is 0.0 (fall-back when fewer than 3 bars).
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

# Maximum acceptable 2-bar move in ATR multiples
_MAX_TICK_JUMP_ATR = 0.8


class TickJumpGuard(BaseTool):
    """
    Tick-jump / price-spike guard.

    A 2-bar move larger than 0.8x ATR suggests a sudden news spike,
    spread widening, or thin-market gap. Entries during these conditions
    carry elevated slippage and reversal risk.
    """

    name = "tick_jump_guard"
    description = "Tick jump guard — blocks entries during abnormal price spikes"

    async def run(self, context) -> ToolResult:
        m15_ind       = context.indicators.get("M15", {})
        tick_jump_atr = m15_ind.get("tick_jump_atr")

        if tick_jump_atr is None:
            return ToolResult(
                passed=True,
                reason="Tick jump data unavailable — skipping",
                severity="info",
            )

        if tick_jump_atr <= _MAX_TICK_JUMP_ATR:
            return ToolResult(
                passed=True,
                reason=(
                    f"2-bar move {tick_jump_atr:.3f}x ATR — "
                    f"within normal range (max {_MAX_TICK_JUMP_ATR}x)"
                ),
                data={"tick_jump_atr": tick_jump_atr, "max_atr": _MAX_TICK_JUMP_ATR},
            )

        return ToolResult(
            passed=False,
            reason=(
                f"Entry blocked: 2-bar spike {tick_jump_atr:.3f}x ATR > "
                f"{_MAX_TICK_JUMP_ATR}x — abnormal price jump, elevated slippage risk"
            ),
            severity="warn",
            data={"tick_jump_atr": tick_jump_atr, "max_atr": _MAX_TICK_JUMP_ATR},
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        tick_jump_atr = m15_ind.get("tick_jump_atr")

        if tick_jump_atr is None:
            return FeatureResult(
                group="volatility",
                features={"price_stability": 50.0},
                meta={"status": "unavailable"},
            )

        # price_stability: 100 = calm market, 0 = extreme spike
        stability = max(0.0, 100.0 - tick_jump_atr / _MAX_TICK_JUMP_ATR * 100)

        return FeatureResult(
            group="volatility",
            features={"price_stability": round(stability, 1)},
            reference_thresholds={"max_tick_jump_atr": _MAX_TICK_JUMP_ATR},
            meta={"tick_jump_atr": tick_jump_atr},
        )
