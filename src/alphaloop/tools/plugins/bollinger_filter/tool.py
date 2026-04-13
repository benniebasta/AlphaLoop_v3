"""
Bollinger Bands filter — avoids entries too close to opposing band.

BUY:  %B must be < 0.7 (price not pressing against the upper band)
SELL: %B must be > 0.3 (price not pressing against the lower band)

%B = (price - lower) / (upper - lower)
  0.0 = at lower band, 0.5 = at midline, 1.0 = at upper band

Fails-open when Bollinger data is unavailable.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

class BollingerFilter(BaseTool):
    """
    Bollinger Bands %B position filter.

    Prevents entries when price is overextended against the relevant band,
    reducing the risk of entering at exhaustion points.
    """

    name = "bollinger_filter"
    description = "Bollinger Bands %B filter — blocks overextended band entries"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        buy_max_pct_b  = self.config.get("buy_max_pct_b", 0.7)
        sell_min_pct_b = self.config.get("sell_min_pct_b", 0.3)

        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})
        pct_b   = m15_ind.get("bb_pct_b")
        bb_upper = m15_ind.get("bb_upper")
        bb_lower = m15_ind.get("bb_lower")

        if pct_b is None:
            return ToolResult(
                passed=True,
                reason="Bollinger Bands unavailable — skipping",
                severity="info",
            )

        if direction == "BUY":
            if pct_b < buy_max_pct_b:
                return ToolResult(
                    passed=True,
                    reason=(
                        f"BB %B {pct_b:.3f} < {buy_max_pct_b} — "
                        f"price not overextended to upper band ({bb_upper})"
                    ),
                    data={"bb_pct_b": pct_b, "bb_upper": bb_upper, "bb_lower": bb_lower},
                )
            return ToolResult(
                passed=False,
                reason=(
                    f"BUY blocked: BB %B {pct_b:.3f} >= {buy_max_pct_b} — "
                    f"price near/above upper band ({bb_upper}) — overextended"
                ),
                severity="warn",
                data={"bb_pct_b": pct_b, "bb_upper": bb_upper, "bb_lower": bb_lower},
            )

        if direction == "SELL":
            if pct_b > sell_min_pct_b:
                return ToolResult(
                    passed=True,
                    reason=(
                        f"BB %B {pct_b:.3f} > {sell_min_pct_b} — "
                        f"price not overextended to lower band ({bb_lower})"
                    ),
                    data={"bb_pct_b": pct_b, "bb_upper": bb_upper, "bb_lower": bb_lower},
                )
            return ToolResult(
                passed=False,
                reason=(
                    f"SELL blocked: BB %B {pct_b:.3f} <= {sell_min_pct_b} — "
                    f"price near/below lower band ({bb_lower}) — overextended"
                ),
                severity="warn",
                data={"bb_pct_b": pct_b, "bb_upper": bb_upper, "bb_lower": bb_lower},
            )

        return ToolResult(
            passed=False,
            reason=f"Unknown direction '{direction}'",
            severity="warn",
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        pct_b = m15_ind.get("bb_pct_b")
        bw = m15_ind.get("bb_band_width")

        if pct_b is None:
            return FeatureResult(
                group="momentum",
                features={"bb_position": 50.0, "bb_bandwidth_norm": 50.0},
                meta={"status": "unavailable"},
            )

        # bb_position: %B scaled to 0-100 (0 = lower band, 100 = upper band)
        bb_position = min(100.0, max(0.0, pct_b * 100))

        # bb_bandwidth_norm: bandwidth normalized (narrow squeeze = low, expansion = high)
        atr_val = m15_ind.get("atr", 1)
        if bw is not None and atr_val and atr_val > 0:
            bw_ratio = bw / atr_val
            bb_bandwidth_norm = min(100.0, max(0.0, bw_ratio * 25))
        else:
            bb_bandwidth_norm = 50.0

        return FeatureResult(
            group="momentum",
            features={
                "bb_position": round(bb_position, 1),
                "bb_bandwidth_norm": round(bb_bandwidth_norm, 1),
            },
            reference_thresholds={"buy_max_pct_b": self.config.get("buy_max_pct_b", 0.7), "sell_min_pct_b": self.config.get("sell_min_pct_b", 0.3)},
            meta={"bb_pct_b": pct_b, "bb_band_width": bw},
        )
