"""
Tick jump guard — detects abnormal 2-bar price spikes.

Blocks entries when the 2-bar price move (|close[-1] - close[-3]|) exceeds
0.8x ATR, which indicates a sudden spike that may retrace or widen spreads.

Does not block if the value is 0.0 (fall-back when fewer than 3 bars).
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

class TickJumpGuard(BaseTool):
    """
    Tick-jump / price-spike guard.

    A 2-bar move larger than max_tick_jump_atr x ATR suggests a sudden news spike,
    spread widening, or thin-market gap. Entries during these conditions
    carry elevated slippage and reversal risk.
    Threshold read from self.config["max_tick_jump_atr"] — set via asset/TF calibration.
    """

    name = "tick_jump_guard"
    description = "Tick jump guard — blocks entries during abnormal price spikes"

    async def run(self, context) -> ToolResult:
        max_tick_jump_atr = self.config.get("max_tick_jump_atr", 0.8)
        m15_ind       = context.indicators.get("M15", {})
        tick_jump_atr = m15_ind.get("tick_jump_atr")

        if tick_jump_atr is None:
            return ToolResult(
                passed=True,
                reason="Tick jump data unavailable — skipping",
                severity="info",
            )

        if tick_jump_atr <= max_tick_jump_atr:
            return ToolResult(
                passed=True,
                reason=(
                    f"2-bar move {tick_jump_atr:.3f}x ATR — "
                    f"within normal range (max {max_tick_jump_atr}x)"
                ),
                data={"tick_jump_atr": tick_jump_atr, "max_atr": max_tick_jump_atr},
            )

        return ToolResult(
            passed=False,
            reason=(
                f"Entry blocked: 2-bar spike {tick_jump_atr:.3f}x ATR > "
                f"{max_tick_jump_atr}x — abnormal price jump, elevated slippage risk"
            ),
            severity="warn",
            data={"tick_jump_atr": tick_jump_atr, "max_atr": max_tick_jump_atr},
        )

    async def extract_features(self, context) -> FeatureResult:
        max_tick_jump_atr = self.config.get("max_tick_jump_atr", 0.8)
        m15_ind = context.indicators.get("M15", {})
        tick_jump_atr = m15_ind.get("tick_jump_atr")

        if tick_jump_atr is None:
            return FeatureResult(
                group="volatility",
                features={"price_stability": 50.0},
                meta={"status": "unavailable"},
            )

        stability = max(0.0, 100.0 - tick_jump_atr / max_tick_jump_atr * 100)

        return FeatureResult(
            group="volatility",
            features={"price_stability": round(stability, 1)},
            reference_thresholds={"max_tick_jump_atr": max_tick_jump_atr},
            meta={"tick_jump_atr": tick_jump_atr},
        )
