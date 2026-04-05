"""
Swing structure filter — validates higher-timeframe market structure.

BUY:  swing_structure must be "bullish" (higher highs + higher lows)
SELL: swing_structure must be "bearish" (lower highs + lower lows)
"ranging" → blocks all directional entries

Fails-open only when the value is None (not computed).
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class SwingStructureFilter(BaseTool):
    """
    Swing structure alignment filter.

    Only allows entries when the M15 swing structure (HH+HL / LH+LL)
    agrees with the signal direction. Blocks entries during ranging phases.
    """

    name = "swing_structure"
    description = "Swing structure alignment — blocks entries against market structure"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind   = context.indicators.get("M15", {})
        structure = m15_ind.get("swing_structure")

        if structure is None:
            return ToolResult(
                passed=True,
                reason="Swing structure unavailable — skipping",
                severity="info",
            )

        if structure == "ranging":
            return ToolResult(
                passed=False,
                reason="Entry blocked: swing structure is 'ranging' — no directional bias",
                severity="warn",
                data={"swing_structure": structure},
            )

        if direction == "BUY":
            if structure == "bullish":
                return ToolResult(
                    passed=True,
                    reason="Swing structure is bullish (HH+HL) — aligned with BUY",
                    bias="bullish",
                    data={"swing_structure": structure},
                )
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: swing structure is '{structure}' — counter-trend",
                severity="warn",
                data={"swing_structure": structure},
            )

        if direction == "SELL":
            if structure == "bearish":
                return ToolResult(
                    passed=True,
                    reason="Swing structure is bearish (LH+LL) — aligned with SELL",
                    bias="bearish",
                    data={"swing_structure": structure},
                )
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: swing structure is '{structure}' — counter-trend",
                severity="warn",
                data={"swing_structure": structure},
            )

        return ToolResult(
            passed=False,
            reason=f"Unknown direction '{direction}'",
            severity="warn",
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        structure = m15_ind.get("swing_structure")

        if structure is None:
            return FeatureResult(
                group="trend",
                features={"structure_alignment": 50.0, "structure_clarity": 50.0},
                meta={"status": "unavailable"},
            )

        # Direction-aware alignment scoring
        direction = getattr(context, "trade_direction", "")
        if direction:
            direction = direction.upper()

        if direction == "BUY":
            # BUY wants bullish structure
            alignment_map = {"bullish": 90.0, "bearish": 10.0, "ranging": 40.0}
        elif direction == "SELL":
            # SELL wants bearish structure
            alignment_map = {"bearish": 90.0, "bullish": 10.0, "ranging": 40.0}
        else:
            # No direction: absolute scoring (legacy)
            alignment_map = {"bullish": 90.0, "bearish": 10.0, "ranging": 50.0}

        alignment = alignment_map.get(structure, 50.0)

        # structure_clarity: ranging = low clarity regardless of direction
        clarity_map = {"bullish": 85.0, "bearish": 85.0, "ranging": 30.0}
        clarity = clarity_map.get(structure, 50.0)

        return FeatureResult(
            group="trend",
            features={
                "structure_alignment": alignment,
                "structure_clarity": clarity,
            },
            meta={
                "swing_structure": structure,
                "scored_direction": direction or "none",
            },
        )
