"""
DXY filter — USD Dollar Index correlation filter for forex/gold.

Blocks gold BUY trades when USD is strongly bullish, and SELL trades
when USD is strongly bearish. Also reduces position size on mild conflicts.

Pipeline order: FOURTH.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class DXYFilter(BaseTool):
    """
    USD Dollar Index correlation filter.

    Reads pre-fetched DXY data from context.dxy and checks whether
    the proposed trade direction conflicts with USD strength.
    """

    name = "dxy_filter"
    description = "DXY correlation filter — blocks conflicting USD/gold trades"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        dxy_data = context.dxy

        if not dxy_data:
            # No DXY data available — pass through
            return ToolResult(
                passed=True,
                reason="DXY data unavailable — skipping",
                data={"dxy": "unavailable"},
            )

        bias = dxy_data.get("bias", "neutral")
        strength = dxy_data.get("strength", 0.0)
        block_dir = dxy_data.get("block_direction")

        # Map DXY bias to tool bias relative to trade direction
        if bias == "bullish_usd":
            tool_bias = "bearish" if direction == "BUY" else "bullish"
        elif bias == "bearish_usd":
            tool_bias = "bullish" if direction == "BUY" else "bearish"
        else:
            tool_bias = "neutral"

        # Block if DXY strongly conflicts with direction
        if block_dir and block_dir == direction and strength >= 0.30:
            return ToolResult(
                passed=False,
                reason=(
                    f"DXY {bias} (strength {strength:.2f}) conflicts with {direction} — "
                    f"1d: {dxy_data.get('change_1d_pct', 0):+.2f}% "
                    f"RSI: {dxy_data.get('rsi', 50):.1f}"
                ),
                severity="block",
                bias=tool_bias,
                size_modifier=0.0,
                data=dxy_data,
            )

        # Reduce size if mild conflict (strength < block threshold)
        size_mod = max(0.5, 1.0 - strength) if block_dir == direction else 1.0

        return ToolResult(
            passed=True,
            reason=f"DXY {bias} — strength={strength:.2f}, size_mod={size_mod:.2f}",
            bias=tool_bias,
            size_modifier=size_mod,
            data=dxy_data,
        )

    async def extract_features(self, context) -> FeatureResult:
        dxy_data = context.dxy

        if not dxy_data:
            return FeatureResult(
                group="trend",
                features={"dxy_alignment": 50.0, "dxy_strength_norm": 50.0},
                meta={"status": "unavailable"},
            )

        bias = dxy_data.get("bias", "neutral")
        strength = float(dxy_data.get("strength", 0))

        # dxy_strength_norm: how strong is USD move (0=none, 100=extreme)
        dxy_strength = min(100.0, strength * 200)

        # dxy_alignment: direction-agnostic
        # For gold: bearish_usd = bullish gold = high score
        if bias == "bearish_usd":
            dxy_alignment = min(100.0, 50 + strength * 100)
        elif bias == "bullish_usd":
            dxy_alignment = max(0.0, 50 - strength * 100)
        else:
            dxy_alignment = 50.0

        return FeatureResult(
            group="trend",
            features={
                "dxy_alignment": round(dxy_alignment, 1),
                "dxy_strength_norm": round(dxy_strength, 1),
            },
            meta=dxy_data,
        )
