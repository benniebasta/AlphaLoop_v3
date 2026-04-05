"""
RSI feature — dual-mode: gate (ALGO_ONLY) or feature (ALGO_AI).

Extracts RSI confirmation logic previously embedded in AlgorithmicSignalEngine.
Provides RSI level, zone analysis, and slope features.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class RSIFeature(BaseTool):
    """
    RSI feature / filter plugin.

    Gate mode: blocks BUY if RSI > 75 (overbought), SELL if RSI < 25 (oversold).
    Feature mode: outputs rsi_level, rsi_zone, and directional score.
    """

    name = "rsi_feature"
    description = "RSI momentum feature — validates overbought/oversold conditions"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15 = context.indicators.get("M15", {})
        rsi_val = m15.get("rsi")

        if rsi_val is None:
            return ToolResult(
                passed=True,
                reason="RSI unavailable — skipping",
                severity="info",
            )

        if direction == "BUY" and rsi_val > 75:
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: RSI {rsi_val:.1f} > 75 — overbought",
                severity="warn",
                data={"rsi": rsi_val},
            )

        if direction == "SELL" and rsi_val < 25:
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: RSI {rsi_val:.1f} < 25 — oversold",
                severity="warn",
                data={"rsi": rsi_val},
            )

        return ToolResult(
            passed=True,
            reason=f"RSI {rsi_val:.1f} — room for {direction.lower()} entry",
            data={"rsi": rsi_val},
        )

    async def extract_features(self, context) -> FeatureResult:
        m15 = context.indicators.get("M15", {})
        rsi_val = m15.get("rsi")

        if rsi_val is None:
            return FeatureResult(
                group="momentum",
                features={"rsi_level": 50.0, "rsi_zone": 50.0},
                meta={"status": "unavailable"},
            )

        # rsi_level: raw RSI already 0-100
        rsi_level = float(rsi_val)

        # rsi_zone: how favorable is the RSI position?
        # For direction-agnostic scoring:
        #   50 = neutral (RSI at 50)
        #   100 = extreme zone (strong signal potential)
        #   Maps distance from 50 to 0-100 scale
        distance_from_neutral = abs(rsi_level - 50.0)
        rsi_zone = min(100.0, distance_from_neutral * 2)

        # rsi_extreme: penalty for being in dangerous zone
        # 0 = safe middle, 100 = extreme (potential reversal)
        if rsi_level >= 70:
            rsi_extreme = min(100.0, (rsi_level - 70) * 3.33)
        elif rsi_level <= 30:
            rsi_extreme = min(100.0, (30 - rsi_level) * 3.33)
        else:
            rsi_extreme = 0.0

        return FeatureResult(
            group="momentum",
            features={
                "rsi_level": round(rsi_level, 1),
                "rsi_zone": round(rsi_zone, 1),
                "rsi_extreme": round(rsi_extreme, 1),
            },
            reference_thresholds={"overbought": 75.0, "oversold": 25.0},
            meta={"rsi": rsi_val},
        )
