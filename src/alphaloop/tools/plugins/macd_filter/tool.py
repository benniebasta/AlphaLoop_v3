"""
MACD filter — momentum confirmation via histogram sign.

BUY:  MACD histogram must be positive (bullish momentum)
SELL: MACD histogram must be negative (bearish momentum)

Fails-open when histogram is unavailable (insufficient bars).
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class MACDFilter(BaseTool):
    """
    MACD histogram momentum alignment filter.

    Ensures entries are taken when MACD momentum agrees with the
    signal direction, reducing false crossovers on flat price action.
    """

    name = "macd_filter"
    description = "MACD momentum confirmation — validates histogram sign"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})
        histogram = m15_ind.get("macd_histogram")

        if histogram is None:
            return ToolResult(
                passed=True,
                reason="MACD histogram unavailable — skipping",
                severity="info",
            )

        if direction == "BUY":
            if histogram > 0:
                return ToolResult(
                    passed=True,
                    reason=f"MACD histogram {histogram:+.6f} is positive — bullish momentum",
                    bias="bullish",
                    data={"macd_histogram": histogram},
                )
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: MACD histogram {histogram:+.6f} is negative — bearish momentum",
                severity="warn",
                data={"macd_histogram": histogram},
            )

        if direction == "SELL":
            if histogram < 0:
                return ToolResult(
                    passed=True,
                    reason=f"MACD histogram {histogram:+.6f} is negative — bearish momentum",
                    bias="bearish",
                    data={"macd_histogram": histogram},
                )
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: MACD histogram {histogram:+.6f} is positive — bullish momentum",
                severity="warn",
                data={"macd_histogram": histogram},
            )

        return ToolResult(
            passed=False,
            reason=f"Unknown direction '{direction}'",
            severity="warn",
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        histogram = m15_ind.get("macd_histogram")

        if histogram is None:
            return FeatureResult(
                group="momentum",
                features={"macd_momentum": 50.0},
                meta={"status": "unavailable"},
            )

        # macd_momentum: 50 = at zero line, >50 = bullish, <50 = bearish
        # Scale histogram to 0-100 using a sigmoid-like mapping
        atr_val = m15_ind.get("atr", 1)
        if atr_val and atr_val > 0:
            normalized = histogram / atr_val * 50
        else:
            normalized = histogram * 1000
        macd_momentum = min(100.0, max(0.0, 50 + normalized))

        return FeatureResult(
            group="momentum",
            features={"macd_momentum": round(macd_momentum, 1)},
            meta={"macd_histogram": histogram},
        )
