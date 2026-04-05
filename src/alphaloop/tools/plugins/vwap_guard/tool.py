"""
VWAP guard — blocks entries overextended from session VWAP.

BUY:  entry must not be > vwap_extension_max_atr above VWAP
SELL: entry must not be > vwap_extension_max_atr below VWAP
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class VWAPGuard(BaseTool):
    """
    VWAP alignment check.

    Rejects signals where the current price is too far from VWAP
    (overextended), reducing edge on pullback setups.
    """

    name = "vwap_guard"
    description = "VWAP alignment check — blocks overextended entries"
    requires_direction = True

    MAX_EXTENSION_ATR = 1.5

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        m15_ind = context.indicators.get("M15", {})

        vwap_val = m15_ind.get("vwap")
        atr_val = m15_ind.get("atr")

        if vwap_val is None or atr_val is None or atr_val == 0:
            return ToolResult(
                passed=True,
                reason="VWAP or ATR unavailable — skipping",
                severity="info",
            )

        # Use current price from context
        current_price = context.price.ask if direction == "BUY" else context.price.bid
        if current_price == 0:
            return ToolResult(
                passed=True,
                reason="Price unavailable — skipping VWAP check",
                severity="info",
            )

        extension = (current_price - vwap_val) / atr_val

        if direction == "BUY" and extension > self.MAX_EXTENSION_ATR:
            return ToolResult(
                passed=False,
                reason=(
                    f"BUY price {current_price:.2f} is {extension:.2f}x ATR above "
                    f"VWAP {vwap_val:.2f} (max={self.MAX_EXTENSION_ATR:.1f}x) — overextended"
                ),
                severity="warn",
                data={"vwap": vwap_val, "atr": atr_val, "extension_atr": round(extension, 3)},
            )

        if direction == "SELL" and extension < -self.MAX_EXTENSION_ATR:
            return ToolResult(
                passed=False,
                reason=(
                    f"SELL price {current_price:.2f} is {abs(extension):.2f}x ATR below "
                    f"VWAP {vwap_val:.2f} (max={self.MAX_EXTENSION_ATR:.1f}x) — overextended"
                ),
                severity="warn",
                data={"vwap": vwap_val, "atr": atr_val, "extension_atr": round(extension, 3)},
            )

        return ToolResult(
            passed=True,
            reason=f"VWAP extension within bounds ({extension:.2f}x ATR)",
            data={"vwap": vwap_val, "atr": atr_val, "extension_atr": round(extension, 3)},
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        vwap_val = m15_ind.get("vwap")
        atr_val = m15_ind.get("atr")

        direction = getattr(context, "trade_direction", "")
        if direction:
            direction = direction.upper()

        # Use directional price
        price_obj = getattr(context, "price", None)
        if price_obj is None:
            price = 0.0
        elif direction == "BUY":
            price = float(getattr(price_obj, "ask", 0) or 0)
        elif direction == "SELL":
            price = float(getattr(price_obj, "bid", 0) or 0)
        else:
            price = float(getattr(price_obj, "ask", 0) or getattr(price_obj, "bid", 0) or 0)

        if vwap_val is None or atr_val is None or atr_val == 0 or price == 0:
            return FeatureResult(
                group="structure",
                features={"vwap_position": 50.0},
                meta={"status": "unavailable"},
            )

        # Signed extension: positive = price above VWAP
        signed_ext = (price - vwap_val) / atr_val

        # Direction-aware scoring:
        #   BUY overextended above VWAP = bad (chasing)
        #   BUY near/below VWAP = good (mean-reversion entry)
        #   SELL overextended below VWAP = bad
        #   SELL near/above VWAP = good
        if direction == "BUY":
            # Positive extension is bad for BUY (overextended up)
            directional_ext = max(0, signed_ext)
        elif direction == "SELL":
            # Negative extension is bad for SELL (overextended down)
            directional_ext = max(0, -signed_ext)
        else:
            directional_ext = abs(signed_ext)

        vwap_position = max(0.0, 100.0 - directional_ext / self.MAX_EXTENSION_ATR * 100)

        return FeatureResult(
            group="structure",
            features={"vwap_position": round(vwap_position, 1)},
            reference_thresholds={"max_extension_atr": self.MAX_EXTENSION_ATR},
            meta={
                "vwap": vwap_val,
                "signed_extension_atr": round(signed_ext, 3),
                "directional_extension_atr": round(directional_ext, 3),
                "scored_direction": direction or "none",
            },
        )
