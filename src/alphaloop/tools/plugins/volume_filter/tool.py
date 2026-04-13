"""
Volume filter — confirms adequate market participation.

Blocks entries when volume_ratio < 0.8 (current bar volume is more than
20% below the 20-bar average), suggesting low participation / thin market.

Fails-open when volume data is unavailable (common for synthetic
instruments on some MT5 brokers that provide no tick_volume).
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

class VolumeFilter(BaseTool):
    """
    Volume participation filter.

    Low-volume entries carry higher slippage risk and are more likely to
    reverse once normal participation resumes. This filter skips signals
    when volume is materially below the recent average.
    """

    name = "volume_filter"
    description = "Volume participation filter — blocks low-volume entries"

    async def run(self, context) -> ToolResult:
        min_vol_ratio = self.config.get("min_vol_ratio", 0.8)

        m15_ind    = context.indicators.get("M15", {})
        vol_ratio  = m15_ind.get("volume_ratio")

        if vol_ratio is None:
            return ToolResult(
                passed=True,
                reason="Volume data unavailable — skipping (broker may not provide tick_volume)",
                severity="info",
            )

        if vol_ratio >= min_vol_ratio:
            return ToolResult(
                passed=True,
                reason=f"Volume ratio {vol_ratio:.2f}x avg — adequate participation",
                data={"volume_ratio": vol_ratio, "min_ratio": min_vol_ratio},
            )

        return ToolResult(
            passed=False,
            reason=(
                f"Entry blocked: volume ratio {vol_ratio:.2f}x is below "
                f"minimum {min_vol_ratio}x — below-average participation"
            ),
            severity="warn",
            data={"volume_ratio": vol_ratio, "min_ratio": min_vol_ratio},
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        vol_ratio = m15_ind.get("volume_ratio")

        if vol_ratio is None:
            return FeatureResult(
                group="volume",
                features={"volume_confirmation": 50.0},
                meta={"status": "unavailable"},
            )

        # Nonlinear mapping: 0.5x=25, 0.8x=50, 1.0x=70, 1.5x=90, 2.0x+=100
        if vol_ratio >= 2.0:
            score = 100.0
        elif vol_ratio >= 1.0:
            score = 70 + (vol_ratio - 1.0) * 30  # 70-100
        elif vol_ratio >= 0.5:
            score = 25 + (vol_ratio - 0.5) * 90  # 25-70
        else:
            score = max(0.0, vol_ratio / 0.5 * 25)  # 0-25

        return FeatureResult(
            group="volume",
            features={"volume_confirmation": round(score, 1)},
            reference_thresholds={"min_vol_ratio": self.config.get("min_vol_ratio", 0.8)},
            meta={"volume_ratio": vol_ratio},
        )
