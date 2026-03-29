"""
BOS guard — Break of Structure confirmation.

Validates that a structural break (close above swing high for BUY,
or close below swing low for SELL) has occurred on the primary timeframe.

Uses close-only confirmation to avoid false wick breaks.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult


class BOSGuard(BaseTool):
    """
    Break of Structure validation tool.

    BUY:  M15 close > swing_high by > min_break_atr x ATR
    SELL: M15 close < swing_low  by > min_break_atr x ATR

    Reads pre-computed BOS data from context.indicators["M15"]["bos"].
    """

    name = "bos_guard"
    description = "Break of structure confirmation — validates structural break"

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})
        bos_data = m15_ind.get("bos")

        if bos_data is None:
            return ToolResult(
                passed=True,
                reason="BOS data unavailable — skipping",
                severity="info",
            )

        if direction == "BUY":
            if bos_data.get("bullish_bos"):
                mult = float(bos_data.get("bullish_break_atr", 0.0))
                return ToolResult(
                    passed=True,
                    reason=(
                        f"BOS confirmed: close broke above swing high "
                        f"{bos_data.get('swing_high')} by {mult:.2f}x ATR"
                    ),
                    bias="bullish",
                    data={
                        "swing_high": bos_data.get("swing_high"),
                        "break_atr_mult": mult,
                    },
                )
            mult = float(bos_data.get("bullish_break_atr", 0.0))
            return ToolResult(
                passed=False,
                reason=(
                    f"No BOS: close below swing high {bos_data.get('swing_high')} "
                    f"(break = {mult:.2f}x ATR, min 0.2x)"
                ),
                severity="warn",
                data={
                    "swing_high": bos_data.get("swing_high"),
                    "break_atr_mult": mult,
                },
            )

        if direction == "SELL":
            if bos_data.get("bearish_bos"):
                mult = float(bos_data.get("bearish_break_atr", 0.0))
                return ToolResult(
                    passed=True,
                    reason=(
                        f"BOS confirmed: close broke below swing low "
                        f"{bos_data.get('swing_low')} by {mult:.2f}x ATR"
                    ),
                    bias="bearish",
                    data={
                        "swing_low": bos_data.get("swing_low"),
                        "break_atr_mult": mult,
                    },
                )
            mult = float(bos_data.get("bearish_break_atr", 0.0))
            return ToolResult(
                passed=False,
                reason=(
                    f"No BOS: close above swing low {bos_data.get('swing_low')} "
                    f"(break = {mult:.2f}x ATR, min 0.2x)"
                ),
                severity="warn",
                data={
                    "swing_low": bos_data.get("swing_low"),
                    "break_atr_mult": mult,
                },
            )

        return ToolResult(
            passed=False,
            reason=f"Unknown direction '{direction}'",
            severity="warn",
        )
