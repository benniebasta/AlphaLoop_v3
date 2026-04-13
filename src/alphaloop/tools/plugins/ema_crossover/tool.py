"""
EMA crossover feature — dual-mode: gate (ALGO_ONLY) or feature (ALGO_AI).

Replaces the hardcoded EMA crossover logic from AlgorithmicSignalEngine
by externalizing it as a composable plugin.

BUY:  fast EMA > slow EMA
SELL: fast EMA < slow EMA
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class EMACrossoverFilter(BaseTool):
    """
    EMA crossover trend alignment filter.

    Gate mode: blocks when EMAs disagree with trade direction.
    Feature mode: outputs spread, alignment, and cross recency scores.
    """

    name = "ema_crossover"
    description = "EMA crossover trend alignment — validates fast/slow EMA relationship"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15 = context.indicators.get("M15", {})
        ema_fast = m15.get("ema_fast") or m15.get("ema21")
        ema_slow = m15.get("ema_slow") or m15.get("ema55")

        if ema_fast is None or ema_slow is None:
            return ToolResult(
                passed=True,
                reason="EMA crossover data unavailable — skipping",
                severity="info",
            )

        spread = ema_fast - ema_slow

        if direction == "BUY":
            if spread > 0:
                return ToolResult(
                    passed=True,
                    reason=f"Fast EMA {ema_fast:.2f} > Slow EMA {ema_slow:.2f} — bullish alignment",
                    bias="bullish",
                    data={"ema_fast": ema_fast, "ema_slow": ema_slow, "spread": round(spread, 5)},
                )
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: Fast EMA {ema_fast:.2f} < Slow EMA {ema_slow:.2f} — bearish",
                severity="warn",
                data={"ema_fast": ema_fast, "ema_slow": ema_slow, "spread": round(spread, 5)},
            )

        if direction == "SELL":
            if spread < 0:
                return ToolResult(
                    passed=True,
                    reason=f"Fast EMA {ema_fast:.2f} < Slow EMA {ema_slow:.2f} — bearish alignment",
                    bias="bearish",
                    data={"ema_fast": ema_fast, "ema_slow": ema_slow, "spread": round(spread, 5)},
                )
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: Fast EMA {ema_fast:.2f} > Slow EMA {ema_slow:.2f} — bullish",
                severity="warn",
                data={"ema_fast": ema_fast, "ema_slow": ema_slow, "spread": round(spread, 5)},
            )

        return ToolResult(passed=False, reason=f"Unknown direction '{direction}'", severity="warn")

    async def extract_features(self, context) -> FeatureResult:
        m15 = context.indicators.get("M15", {})
        ema_fast = m15.get("ema_fast") or m15.get("ema21")
        ema_slow = m15.get("ema_slow") or m15.get("ema55")
        atr_val = m15.get("atr", 0)

        if ema_fast is None or ema_slow is None or not atr_val:
            return FeatureResult(
                group="trend",
                features={"ema_spread": 50.0, "ema_alignment": 50.0},
                meta={"status": "unavailable"},
            )

        spread = ema_fast - ema_slow
        # Spread proximity: 100=tight/fresh crossover, 0=wide divergence
        spread_norm = max(0.0, min(100.0, 100.0 - abs(spread) / atr_val * 25))

        # Alignment: 100 if fast > slow (bullish bias), 0 if fast < slow
        alignment = min(100.0, max(0.0, 50 + (spread / atr_val) * 25))

        return FeatureResult(
            group="trend",
            features={
                "ema_spread": round(spread_norm, 1),
                "ema_alignment": round(alignment, 1),
            },
            meta={"ema_fast": ema_fast, "ema_slow": ema_slow, "spread": round(spread, 5)},
        )
