"""
ALMA filter — Arnaud Legoux Moving Average trend alignment.

ALMA uses a Gaussian-weighted offset kernel that reduces lag compared to
standard MAs while maintaining smoothness. Offset=0.85 weights recent
prices, sigma=6 controls the Gaussian width.

Gate mode: blocks BUY if price < ALMA, SELL if price > ALMA.
Feature mode: outputs distance and alignment scores.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class ALMAFilter(BaseTool):
    """
    Arnaud Legoux Moving Average trend alignment filter.

    Gate mode: blocks counter-trend entries relative to ALMA.
    Feature mode: outputs price-ALMA distance and alignment scores.
    """

    name = "alma_filter"
    description = "ALMA trend alignment — blocks counter-trend entries vs Arnaud Legoux MA"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15 = context.indicators.get("M15", {})
        alma_val = m15.get("alma")

        if alma_val is None:
            return ToolResult(
                passed=True,
                reason="ALMA unavailable — skipping",
                severity="info",
            )

        current_price = (
            context.price.ask if direction == "BUY" else context.price.bid
        )
        if current_price == 0:
            return ToolResult(
                passed=True,
                reason="Price unavailable — skipping ALMA check",
                severity="info",
            )

        if direction == "BUY":
            if current_price > alma_val:
                return ToolResult(
                    passed=True,
                    reason=f"Price {current_price:.2f} > ALMA {alma_val:.2f} — bullish alignment",
                    bias="bullish",
                    data={"alma": alma_val, "price": current_price},
                )
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: price {current_price:.2f} < ALMA {alma_val:.2f} — bearish",
                severity="warn",
                data={"alma": alma_val, "price": current_price},
            )

        if direction == "SELL":
            if current_price < alma_val:
                return ToolResult(
                    passed=True,
                    reason=f"Price {current_price:.2f} < ALMA {alma_val:.2f} — bearish alignment",
                    bias="bearish",
                    data={"alma": alma_val, "price": current_price},
                )
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: price {current_price:.2f} > ALMA {alma_val:.2f} — bullish",
                severity="warn",
                data={"alma": alma_val, "price": current_price},
            )

        return ToolResult(passed=False, reason=f"Unknown direction '{direction}'", severity="warn")

    async def extract_features(self, context) -> FeatureResult:
        m15 = context.indicators.get("M15", {})
        alma_val = m15.get("alma")
        atr_val = m15.get("atr", 0)
        price = context.price.ask if context.price.ask > 0 else context.price.bid

        if alma_val is None or price == 0 or not atr_val:
            return FeatureResult(
                group="trend",
                features={"alma_distance": 50.0, "alma_alignment": 50.0},
                meta={"status": "unavailable"},
            )

        distance = price - alma_val
        # Normalize distance as fraction of ATR
        distance_atr = distance / atr_val if atr_val > 0 else 0

        # alma_distance: absolute distance normalized (0=at ALMA, 100=far away)
        alma_distance = min(100.0, abs(distance_atr) * 20)

        # alma_alignment: 100 = far above ALMA (bullish), 0 = far below (bearish), 50 = at ALMA
        alma_alignment = min(100.0, max(0.0, 50 + distance_atr * 20))

        return FeatureResult(
            group="trend",
            features={
                "alma_distance": round(alma_distance, 1),
                "alma_alignment": round(alma_alignment, 1),
            },
            meta={"alma": alma_val, "price": price, "distance_atr": round(distance_atr, 3)},
        )
