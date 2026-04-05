"""
EMA200 filter — trend alignment with the 200-period EMA.

BUY:  current price must be above EMA200 (uptrend)
SELL: current price must be below EMA200 (downtrend)

Fails-open when fewer than 200 M15 bars are available.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class EMA200Filter(BaseTool):
    """
    200-period EMA trend alignment filter.

    Ensures trades are taken in the direction of the long-term M15 trend.
    Rejects counter-trend signals when sufficient data is present.
    """

    name = "ema200_filter"
    description = "EMA200 trend alignment — blocks counter-trend entries"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})
        ema200 = m15_ind.get("ema200")

        if ema200 is None:
            return ToolResult(
                passed=True,
                reason="EMA200 unavailable — skipping (fewer than 200 M15 bars)",
                severity="info",
            )

        # Use ask for BUY entries, bid for SELL entries
        current_price = (
            context.price.ask if direction == "BUY" else context.price.bid
        )
        if current_price == 0:
            return ToolResult(
                passed=True,
                reason="Price unavailable — skipping EMA200 check",
                severity="info",
            )

        if direction == "BUY":
            if current_price > ema200:
                return ToolResult(
                    passed=True,
                    reason=(
                        f"Price {current_price:.4f} is above EMA200 {ema200:.4f} — uptrend confirmed"
                    ),
                    bias="bullish",
                    data={"ema200": ema200, "price": current_price},
                )
            return ToolResult(
                passed=False,
                reason=(
                    f"BUY blocked: price {current_price:.4f} is below EMA200 {ema200:.4f} — downtrend"
                ),
                severity="warn",
                data={"ema200": ema200, "price": current_price},
            )

        if direction == "SELL":
            if current_price < ema200:
                return ToolResult(
                    passed=True,
                    reason=(
                        f"Price {current_price:.4f} is below EMA200 {ema200:.4f} — downtrend confirmed"
                    ),
                    bias="bearish",
                    data={"ema200": ema200, "price": current_price},
                )
            return ToolResult(
                passed=False,
                reason=(
                    f"SELL blocked: price {current_price:.4f} is above EMA200 {ema200:.4f} — uptrend"
                ),
                severity="warn",
                data={"ema200": ema200, "price": current_price},
            )

        return ToolResult(
            passed=False,
            reason=f"Unknown direction '{direction}'",
            severity="warn",
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        ema200 = m15_ind.get("ema200")
        price = context.price.ask if context.price.ask > 0 else context.price.bid

        if ema200 is None or price == 0:
            return FeatureResult(
                group="trend",
                features={"ema200_position": 50.0},
                meta={"status": "unavailable"},
            )

        distance_pct = ((price - ema200) / ema200) * 100
        # 100 = far above EMA200 (strong uptrend), 0 = far below, 50 = at EMA
        position = min(100.0, max(0.0, 50 + distance_pct * 10))

        return FeatureResult(
            group="trend",
            features={"ema200_position": round(position, 1)},
            reference_thresholds={"rule": "price > ema200 for BUY"},
            meta={"ema200": ema200, "price": price, "distance_pct": round(distance_pct, 4)},
        )
