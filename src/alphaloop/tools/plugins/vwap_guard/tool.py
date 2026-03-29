"""
VWAP guard — blocks entries overextended from session VWAP.

BUY:  entry must not be > vwap_extension_max_atr above VWAP
SELL: entry must not be > vwap_extension_max_atr below VWAP
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult


class VWAPGuard(BaseTool):
    """
    VWAP alignment check.

    Rejects signals where the current price is too far from VWAP
    (overextended), reducing edge on pullback setups.
    """

    name = "vwap_guard"
    description = "VWAP alignment check — blocks overextended entries"

    MAX_EXTENSION_ATR = 1.5

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})

        vwap_val = m15_ind.get("vwap")
        atr_val = m15_ind.get("atr")

        if vwap_val is None or atr_val is None or atr_val == 0:
            return ToolResult(
                passed=True,
                reason="VWAP or ATR unavailable — skipping",
                severity="info",
            )

        # Use current price from context
        current_price = context.price.ask if direction == "BUY" else context.price.bid
        if current_price == 0:
            return ToolResult(
                passed=True,
                reason="Price unavailable — skipping VWAP check",
                severity="info",
            )

        extension = (current_price - vwap_val) / atr_val

        if direction == "BUY" and extension > self.MAX_EXTENSION_ATR:
            return ToolResult(
                passed=False,
                reason=(
                    f"BUY price {current_price:.2f} is {extension:.2f}x ATR above "
                    f"VWAP {vwap_val:.2f} (max={self.MAX_EXTENSION_ATR:.1f}x) — overextended"
                ),
                severity="warn",
                data={"vwap": vwap_val, "atr": atr_val, "extension_atr": round(extension, 3)},
            )

        if direction == "SELL" and extension < -self.MAX_EXTENSION_ATR:
            return ToolResult(
                passed=False,
                reason=(
                    f"SELL price {current_price:.2f} is {abs(extension):.2f}x ATR below "
                    f"VWAP {vwap_val:.2f} (max={self.MAX_EXTENSION_ATR:.1f}x) — overextended"
                ),
                severity="warn",
                data={"vwap": vwap_val, "atr": atr_val, "extension_atr": round(extension, 3)},
            )

        return ToolResult(
            passed=True,
            reason=f"VWAP extension within bounds ({extension:.2f}x ATR)",
            data={"vwap": vwap_val, "atr": atr_val, "extension_atr": round(extension, 3)},
        )
