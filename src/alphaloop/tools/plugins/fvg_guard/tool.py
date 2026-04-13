"""
FVG guard — Fair Value Gap confirmation.

Validates that a fair value gap (3-candle imbalance zone) exists on the
primary timeframe in the direction of the proposed trade.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class FVGGuard(BaseTool):
    """
    Fair Value Gap entry zone validation tool.

    BUY:  A bullish FVG must exist on M15 with minimum size threshold.
    SELL: A bearish FVG must exist on M15 with minimum size threshold.

    Reads pre-computed FVG data from context.indicators["M15"]["fvg"].
    """

    name = "fvg_guard"
    description = "Fair value gap confirmation — validates imbalance zones"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        min_size_atr = self.config.get("min_size_atr", 0.15)
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})
        fvg_data = m15_ind.get("fvg")

        if fvg_data is None:
            return ToolResult(
                passed=True,
                reason="FVG data unavailable — skipping",
                severity="info",
            )

        if direction == "BUY":
            gaps = fvg_data.get("bullish", [])
            label = "bullish"
        elif direction == "SELL":
            gaps = fvg_data.get("bearish", [])
            label = "bearish"
        else:
            return ToolResult(
                passed=False,
                reason=f"Unknown direction '{direction}'",
                severity="warn",
            )

        candidates = [g for g in gaps if g.get("size_atr", 0) >= min_size_atr]

        if not candidates:
            return ToolResult(
                passed=False,
                reason=(
                    f"No {label} FVG >= {min_size_atr}x ATR on M15 — "
                    f"no imbalance zone for {direction.lower()}"
                ),
                severity="warn",
                data={"available_fvgs": len(gaps), "min_size_atr": min_size_atr},
            )

        # Most recent qualifying FVG
        best = candidates[-1]
        return ToolResult(
            passed=True,
            reason=(
                f"{label.capitalize()} FVG found [{best['bottom']:.5g}-{best['top']:.5g}], "
                f"size={best.get('size_atr', 0):.2f}x ATR"
            ),
            bias="bullish" if direction == "BUY" else "bearish",
            data={
                "fvg_bottom": best["bottom"],
                "fvg_top": best["top"],
                "midpoint": best.get("midpoint"),
                "size_atr": best.get("size_atr"),
            },
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        fvg_data = m15_ind.get("fvg")

        if fvg_data is None:
            return FeatureResult(
                group="structure",
                features={"fvg_presence": 50.0, "fvg_quality": 50.0},
                meta={"status": "unavailable"},
            )

        bull_gaps = fvg_data.get("bullish", [])
        bear_gaps = fvg_data.get("bearish", [])

        # Direction-aware: score FVGs in the trade direction
        direction = getattr(context, "trade_direction", "")
        if direction:
            direction = direction.upper()

        if direction == "BUY":
            gaps = bull_gaps
        elif direction == "SELL":
            gaps = bear_gaps
        else:
            gaps = bull_gaps + bear_gaps  # legacy: all gaps

        if not gaps:
            return FeatureResult(
                group="structure",
                features={"fvg_presence": 50.0, "fvg_quality": 50.0},
                meta={
                    "bullish_count": len(bull_gaps),
                    "bearish_count": len(bear_gaps),
                    "scored_direction": direction or "none",
                },
            )

        # fvg_presence: 50=neutral, 50-100 scaled by gap count
        fvg_presence = min(100.0, 50.0 + len(gaps) * 25.0)

        # fvg_quality: 50=neutral, 50-100 scaled by best gap size in ATR
        best_size = max(g.get("size_atr", 0) for g in gaps)
        fvg_quality = min(100.0, 50.0 + best_size / 0.5 * 50.0)

        return FeatureResult(
            group="structure",
            features={
                "fvg_presence": round(fvg_presence, 1),
                "fvg_quality": round(fvg_quality, 1),
            },
            meta={
                "bullish_count": len(bull_gaps),
                "bearish_count": len(bear_gaps),
                "best_size_atr": best_size,
                "scored_direction": direction or "none",
            },
        )
