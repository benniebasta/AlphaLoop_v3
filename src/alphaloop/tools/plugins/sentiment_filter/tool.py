"""
Sentiment filter — Polymarket macro sentiment alignment check.

Does NOT block trades — only reduces position size when macro sentiment
conflicts with the trade direction.

Pipeline order: FIFTH.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult


class SentimentFilter(BaseTool):
    """
    Polymarket macro sentiment filter.

    Reads pre-fetched sentiment from context.sentiment and checks
    alignment with trade direction. Reduces size on conflict but
    never outright blocks.
    """

    name = "sentiment_filter"
    description = "Polymarket sentiment alignment — reduces size on conflict"

    async def run(self, context) -> ToolResult:
        direction = context.trade_direction.upper()
        sentiment = context.sentiment

        if not sentiment:
            return ToolResult(
                passed=True,
                reason="Sentiment data unavailable — skipping",
                data={"sentiment": "unavailable"},
            )

        bias = sentiment.get("bias", "neutral")
        try:
            confidence = max(0.0, min(1.0, float(sentiment.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5

        conflict = (
            (direction == "BUY" and bias == "bearish")
            or (direction == "SELL" and bias == "bullish")
        )

        if conflict:
            size_mod = round(max(0.5, 1.0 - confidence * 0.5), 2)
        else:
            size_mod = 1.0

        return ToolResult(
            passed=True,  # sentiment never blocks — only reduces size
            reason=(
                f"Polymarket sentiment: {bias} "
                f"(confidence={confidence:.0%}, "
                f"markets={sentiment.get('markets_found', 0)}, "
                f"size_mod={size_mod:.2f})"
            ),
            bias=bias,
            size_modifier=size_mod,
            data=sentiment,
        )
