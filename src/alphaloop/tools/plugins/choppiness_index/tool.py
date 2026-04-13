"""
Choppiness Index — measures trending vs ranging market conditions.

CI = 100 * log10(sum(ATR,N) / (HH-LL)) / log10(N)
  >61.8 = choppy / consolidating → blocks directional entries
  <38.2 = strongly trending → ideal for trend-following

Gate mode: blocks entries when CI > 61.8 (market too choppy).
Feature mode: outputs trendiness score (inverse of choppiness).
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

class ChoppinessIndexFilter(BaseTool):
    """
    Choppiness Index market regime filter.

    Gate mode: blocks directional entries in choppy markets (CI > 61.8).
    Feature mode: outputs trendiness = 100 - CI.
    """

    name = "choppiness_index"
    description = "Choppiness Index — blocks entries in ranging/choppy markets"

    async def run(self, context) -> ToolResult:
        m15 = context.indicators.get("M15", {})
        chop_data = m15.get("choppiness")

        choppy_threshold   = self.config.get("choppy_threshold", 61.8)
        trending_threshold = self.config.get("trending_threshold", 38.2)

        if chop_data is None:
            return ToolResult(
                passed=True,
                reason="Choppiness Index unavailable — skipping",
                severity="info",
            )

        ci = float(chop_data.get("ci", 50))

        if ci > choppy_threshold:
            return ToolResult(
                passed=False,
                reason=(
                    f"Entry blocked: CI {ci:.1f} > {choppy_threshold} — "
                    f"market is choppy/ranging, no directional edge"
                ),
                severity="warn",
                data=chop_data,
            )

        regime = "trending" if ci < trending_threshold else "transitional"
        return ToolResult(
            passed=True,
            reason=f"CI {ci:.1f} — market is {regime}",
            data=chop_data,
        )

    async def extract_features(self, context) -> FeatureResult:
        m15 = context.indicators.get("M15", {})
        chop_data = m15.get("choppiness")

        if chop_data is None:
            return FeatureResult(
                group="volatility",
                features={"trendiness": 50.0},
                meta={"status": "unavailable"},
            )

        ci = float(chop_data.get("ci", 50))

        # trendiness: inverse of CI (100 = strongly trending, 0 = max choppiness)
        trendiness = max(0.0, min(100.0, 100.0 - ci))

        return FeatureResult(
            group="volatility",
            features={"trendiness": round(trendiness, 1)},
            reference_thresholds={
                "choppy_above": self.config.get("choppy_threshold", 61.8),
                "trending_below": self.config.get("trending_threshold", 38.2),
            },
            meta=chop_data,
        )
