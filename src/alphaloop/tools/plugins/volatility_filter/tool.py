"""
Volatility filter — ATR-based volatility gate.

Pipeline order: THIRD — cheap check before external API calls.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class VolatilityFilter(BaseTool):
    """
    ATR-based volatility gate.

    Checks H1 ATR% against acceptable range:
      - Too high (spike): block
      - Too low (dead): block
      - Elevated (approaching max): reduce size to 70%
      - Normal: pass
    """

    name = "volatility_filter"
    description = "ATR-based volatility check — blocks extreme or dead markets"

    async def run(self, context) -> ToolResult:
        max_atr_pct = self.config.get("max_atr_pct", 2.5)
        min_atr_pct = self.config.get("min_atr_pct", 0.05)
        soft_limit = max_atr_pct * 0.80

        # Get H1 indicators
        h1_ind = context.indicators.get("H1", {})
        atr_pct = h1_ind.get("atr_pct", 0.0) or 0.0
        atr_val = h1_ind.get("atr", 0.0) or 0.0

        if atr_pct == 0.0:
            return ToolResult(
                passed=False,
                reason="ATR data unavailable — fail-safe block",
                severity="block",
                size_modifier=0.0,
                data={"atr_pct": 0, "regime": "unknown"},
            )

        if atr_pct > max_atr_pct:
            return ToolResult(
                passed=False,
                reason=f"ATR spike: {atr_pct:.3f}% > max {max_atr_pct:.2f}%",
                severity="block",
                size_modifier=0.0,
                data={"atr_pct": atr_pct, "atr": atr_val, "regime": "extreme"},
            )

        if atr_pct < min_atr_pct:
            return ToolResult(
                passed=False,
                reason=f"Dead market: ATR {atr_pct:.3f}% < min {min_atr_pct:.3f}%",
                severity="block",
                size_modifier=0.0,
                data={"atr_pct": atr_pct, "atr": atr_val, "regime": "dead"},
            )

        if atr_pct > soft_limit:
            return ToolResult(
                passed=True,
                reason=(
                    f"Elevated volatility: ATR {atr_pct:.3f}% > "
                    f"soft limit {soft_limit:.2f}% — reducing size to 70%"
                ),
                size_modifier=0.7,
                data={"atr_pct": atr_pct, "atr": atr_val, "regime": "elevated"},
            )

        return ToolResult(
            passed=True,
            reason=f"Normal volatility: ATR={atr_pct:.3f}%",
            data={"atr_pct": atr_pct, "atr": atr_val, "regime": "normal"},
        )

    async def extract_features(self, context) -> FeatureResult:
        h1_ind = context.indicators.get("H1", {})
        atr_pct = h1_ind.get("atr_pct", 0.0) or 0.0
        atr_val = h1_ind.get("atr", 0.0) or 0.0

        if atr_pct == 0.0:
            return FeatureResult(
                group="volatility",
                features={"volatility_regime": 0.0},
                meta={"status": "unavailable"},
            )

        max_atr_pct = self.config.get("max_atr_pct", 2.5)
        min_atr_pct = self.config.get("min_atr_pct", 0.05)

        if atr_pct > max_atr_pct:
            score = max(0.0, 100 - (atr_pct - max_atr_pct) / max_atr_pct * 100)
        elif atr_pct < min_atr_pct:
            score = max(0.0, atr_pct / min_atr_pct * 20)
        else:
            mid = (max_atr_pct + min_atr_pct) / 2
            dist = abs(atr_pct - mid) / (max_atr_pct - min_atr_pct)
            score = max(50.0, 100 - dist * 100)

        return FeatureResult(
            group="volatility",
            features={"volatility_regime": round(min(100.0, score), 1)},
            reference_thresholds={"max_atr_pct": max_atr_pct, "min_atr_pct": min_atr_pct},
            meta={"atr_pct": atr_pct, "atr": atr_val},
        )
