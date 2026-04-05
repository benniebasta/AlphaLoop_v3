"""
Fast Fingers Study — momentum exhaustion / reversal detector.

Uses Rate of Change + standard deviation bands to identify overextended
moves that may snap back. Blocks entries when momentum is exhausted
in the trade direction.

Gate mode: blocks BUY if upside exhausted, SELL if downside exhausted.
Feature mode: outputs momentum_freshness and roc_position scores.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class FastFingersFilter(BaseTool):
    """
    Fast Fingers momentum exhaustion filter.

    Gate mode: blocks when ROC breaches stddev bands in trade direction.
    Feature mode: outputs freshness score (inverse of exhaustion).
    """

    name = "fast_fingers"
    description = "Fast Fingers momentum exhaustion — blocks overextended entries"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15 = context.indicators.get("M15", {})
        ff_data = m15.get("fast_fingers")

        if ff_data is None:
            return ToolResult(
                passed=True,
                reason="Fast Fingers data unavailable — skipping",
                severity="info",
            )

        if direction == "BUY" and ff_data.get("is_exhausted_up"):
            return ToolResult(
                passed=False,
                reason=(
                    f"BUY blocked: upside momentum exhausted "
                    f"(ROC={ff_data['roc']:.2f}% > upper band {ff_data['upper_band']:.2f}%)"
                ),
                severity="warn",
                data=ff_data,
            )

        if direction == "SELL" and ff_data.get("is_exhausted_down"):
            return ToolResult(
                passed=False,
                reason=(
                    f"SELL blocked: downside momentum exhausted "
                    f"(ROC={ff_data['roc']:.2f}% < lower band {ff_data['lower_band']:.2f}%)"
                ),
                severity="warn",
                data=ff_data,
            )

        return ToolResult(
            passed=True,
            reason=f"Momentum not exhausted (ROC={ff_data.get('roc', 0):.2f}%)",
            data=ff_data,
        )

    async def extract_features(self, context) -> FeatureResult:
        m15 = context.indicators.get("M15", {})
        ff_data = m15.get("fast_fingers")

        if ff_data is None:
            return FeatureResult(
                group="momentum",
                features={"momentum_freshness": 50.0, "roc_position": 50.0},
                meta={"status": "unavailable"},
            )

        exhaustion = float(ff_data.get("exhaustion_score", 0))

        # momentum_freshness: inverse of exhaustion (100 = fresh, 0 = exhausted)
        momentum_freshness = max(0.0, 100.0 - exhaustion)

        # roc_position: where ROC sits within bands
        # Normalize ROC relative to bands: 50 = at SMA, 0 = at/below lower, 100 = at/above upper
        roc = float(ff_data.get("roc", 0))
        upper = float(ff_data.get("upper_band", 1))
        lower = float(ff_data.get("lower_band", -1))
        band_range = upper - lower
        if band_range > 0:
            roc_position = min(100.0, max(0.0, (roc - lower) / band_range * 100))
        else:
            roc_position = 50.0

        return FeatureResult(
            group="momentum",
            features={
                "momentum_freshness": round(momentum_freshness, 1),
                "roc_position": round(roc_position, 1),
            },
            meta=ff_data,
        )
