"""
ADX filter — trend strength gate.

Blocks directional entries when ADX < 20, indicating a ranging/choppy
market where trend-following strategies perform poorly.

Fails-open when ADX data is unavailable.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

# Minimum ADX for a valid directional trade
_MIN_ADX = 20.0


class ADXFilter(BaseTool):
    """
    ADX (Average Directional Index) trend strength filter.

    Ensures the market is trending before allowing directional entries.
    ADX below threshold indicates consolidation / choppy conditions.
    """

    name = "adx_filter"
    description = "ADX trend strength gate — blocks entries in ranging markets"

    async def run(self, context) -> ToolResult:
        m15_ind = context.indicators.get("M15", {})
        adx_val = m15_ind.get("adx")

        if adx_val is None:
            return ToolResult(
                passed=True,
                reason="ADX unavailable — skipping",
                severity="info",
            )

        if adx_val >= _MIN_ADX:
            return ToolResult(
                passed=True,
                reason=f"ADX {adx_val:.1f} >= {_MIN_ADX} — trending market, entry allowed",
                data={"adx": adx_val, "min_adx": _MIN_ADX},
            )

        return ToolResult(
            passed=False,
            reason=(
                f"Entry blocked: ADX {adx_val:.1f} < {_MIN_ADX} — "
                f"market is ranging/choppy, no directional edge"
            ),
            severity="warn",
            data={"adx": adx_val, "min_adx": _MIN_ADX},
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        adx_val = m15_ind.get("adx")
        plus_di = m15_ind.get("adx_plus_di")
        minus_di = m15_ind.get("adx_minus_di")

        if adx_val is None:
            return FeatureResult(
                group="momentum",
                features={"adx_strength": 50.0, "di_alignment": 50.0},
                meta={"status": "unavailable"},
            )

        # adx_strength: raw ADX clamped to 0-100 (already in that range)
        adx_strength = min(100.0, max(0.0, float(adx_val)))

        # di_alignment: 100 if +DI dominates (bullish), 0 if -DI dominates, 50 = equal
        if plus_di is not None and minus_di is not None:
            di_sum = plus_di + minus_di
            di_alignment = (plus_di / di_sum * 100) if di_sum > 0 else 50.0
        else:
            di_alignment = 50.0

        return FeatureResult(
            group="momentum",
            features={
                "adx_strength": round(adx_strength, 1),
                "di_alignment": round(di_alignment, 1),
            },
            reference_thresholds={"min_adx": _MIN_ADX},
            meta={"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di},
        )
