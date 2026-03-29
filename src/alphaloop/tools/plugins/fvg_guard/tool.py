"""
FVG guard — Fair Value Gap confirmation.

Validates that a fair value gap (3-candle imbalance zone) exists on the
primary timeframe in the direction of the proposed trade.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult


class FVGGuard(BaseTool):
    """
    Fair Value Gap entry zone validation tool.

    BUY:  A bullish FVG must exist on M15 with minimum size threshold.
    SELL: A bearish FVG must exist on M15 with minimum size threshold.

    Reads pre-computed FVG data from context.indicators["M15"]["fvg"].
    """

    name = "fvg_guard"
    description = "Fair value gap confirmation — validates imbalance zones"

    MIN_SIZE_ATR = 0.15

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})
        fvg_data = m15_ind.get("fvg")

        if fvg_data is None:
            return ToolResult(
                passed=True,
                reason="FVG data unavailable — skipping",
                severity="info",
            )

        if direction == "BUY":
            gaps = fvg_data.get("bullish", [])
            label = "bullish"
        elif direction == "SELL":
            gaps = fvg_data.get("bearish", [])
            label = "bearish"
        else:
            return ToolResult(
                passed=False,
                reason=f"Unknown direction '{direction}'",
                severity="warn",
            )

        # Filter by minimum size
        candidates = [g for g in gaps if g.get("size_atr", 0) >= self.MIN_SIZE_ATR]

        if not candidates:
            return ToolResult(
                passed=False,
                reason=(
                    f"No {label} FVG >= {self.MIN_SIZE_ATR}x ATR on M15 — "
                    f"no imbalance zone for {direction.lower()}"
                ),
                severity="warn",
                data={"available_fvgs": len(gaps), "min_size_atr": self.MIN_SIZE_ATR},
            )

        # Most recent qualifying FVG
        best = candidates[-1]
        return ToolResult(
            passed=True,
            reason=(
                f"{label.capitalize()} FVG found [{best['bottom']:.5g}-{best['top']:.5g}], "
                f"size={best.get('size_atr', 0):.2f}x ATR"
            ),
            bias="bullish" if direction == "BUY" else "bearish",
            data={
                "fvg_bottom": best["bottom"],
                "fvg_top": best["top"],
                "midpoint": best.get("midpoint"),
                "size_atr": best.get("size_atr"),
            },
        )
