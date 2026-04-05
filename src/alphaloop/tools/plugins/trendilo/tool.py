"""
Trendilo filter — smoothed linear regression slope for trend detection.

Detects trend direction and strength by applying EMA smoothing to
linear regression slope. Normalized against ATR for cross-asset
comparability.

Gate mode: blocks entries against the detected trend direction.
Feature mode: outputs trend_strength (0-100) and trend_alignment.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class TrendiloFilter(BaseTool):
    """
    Trendilo trend detection filter.

    Gate mode: blocks BUY if slope is down, SELL if slope is up.
    Feature mode: outputs trend_strength and alignment score.
    """

    name = "trendilo"
    description = "Trendilo trend detection — blocks counter-trend entries via regression slope"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15 = context.indicators.get("M15", {})
        trendilo_data = m15.get("trendilo")

        if trendilo_data is None:
            return ToolResult(
                passed=True,
                reason="Trendilo data unavailable — skipping",
                severity="info",
            )

        slope_dir = trendilo_data.get("direction", "flat")
        strength = trendilo_data.get("strength", 0)

        if direction == "BUY" and slope_dir == "down" and strength > 30:
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: Trendilo slope is down (strength={strength:.1f})",
                severity="warn",
                data=trendilo_data,
            )

        if direction == "SELL" and slope_dir == "up" and strength > 30:
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: Trendilo slope is up (strength={strength:.1f})",
                severity="warn",
                data=trendilo_data,
            )

        return ToolResult(
            passed=True,
            reason=f"Trendilo {slope_dir} (strength={strength:.1f}) — aligned with {direction}",
            bias="bullish" if slope_dir == "up" else ("bearish" if slope_dir == "down" else "neutral"),
            data=trendilo_data,
        )

    async def extract_features(self, context) -> FeatureResult:
        m15 = context.indicators.get("M15", {})
        trendilo_data = m15.get("trendilo")

        if trendilo_data is None:
            return FeatureResult(
                group="trend",
                features={"trend_strength": 50.0, "trend_alignment": 50.0},
                meta={"status": "unavailable"},
            )

        strength = float(trendilo_data.get("strength", 0))
        slope_dir = trendilo_data.get("direction", "flat")

        # trend_strength: raw strength 0-100 (already normalized in indicator)
        trend_strength = min(100.0, strength)

        # trend_alignment: direction-agnostic
        # 100 = strong up, 0 = strong down, 50 = flat
        if slope_dir == "up":
            trend_alignment = min(100.0, 50 + strength / 2)
        elif slope_dir == "down":
            trend_alignment = max(0.0, 50 - strength / 2)
        else:
            trend_alignment = 50.0

        return FeatureResult(
            group="trend",
            features={
                "trend_strength": round(trend_strength, 1),
                "trend_alignment": round(trend_alignment, 1),
            },
            meta=trendilo_data,
        )
