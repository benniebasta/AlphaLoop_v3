"""
Liquidity vacuum guard — detects thin-body / large-wick candles.

Blocks entries when BOTH conditions are true:
  - bar_range_atr > 2.5  (unusually large candle range)
  - body_pct < 30        (body is less than 30% of the bar range)

This pattern (large wicks, tiny body) indicates a liquidity vacuum where
price swept stop levels but found no follow-through. Entering immediately
after such candles carries high reversal risk.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

_MAX_RANGE_ATR = 2.5   # bar range threshold in ATR multiples
_MIN_BODY_PCT  = 30.0  # minimum body-to-range percentage


class LiqVacuumGuard(BaseTool):
    """
    Liquidity vacuum candle guard.

    Identifies doji-like candles with abnormally large total range —
    a hallmark of stop-sweep or illiquid spike conditions.
    Both conditions must trigger simultaneously; a large-bodied
    candle (strong momentum) is NOT blocked even if range is large.
    """

    name = "liq_vacuum_guard"
    description = "Liquidity vacuum guard — blocks entries on thin-body spike candles"

    async def run(self, context) -> ToolResult:
        m15_ind    = context.indicators.get("M15", {})
        liq_data   = m15_ind.get("liq_vacuum")

        if liq_data is None:
            return ToolResult(
                passed=True,
                reason="Liquidity vacuum data unavailable — skipping",
                severity="info",
            )

        bar_range_atr = float(liq_data.get("bar_range_atr", 0.0))
        body_pct      = float(liq_data.get("body_pct", 100.0))

        is_large_range = bar_range_atr > _MAX_RANGE_ATR
        is_thin_body   = body_pct < _MIN_BODY_PCT

        if is_large_range and is_thin_body:
            return ToolResult(
                passed=False,
                reason=(
                    f"Entry blocked: liquidity vacuum candle — "
                    f"range {bar_range_atr:.2f}x ATR > {_MAX_RANGE_ATR}x "
                    f"and body only {body_pct:.1f}% < {_MIN_BODY_PCT}% — stop-sweep / thin market"
                ),
                severity="warn",
                data={
                    "bar_range_atr": bar_range_atr,
                    "body_pct":      body_pct,
                    "max_range_atr": _MAX_RANGE_ATR,
                    "min_body_pct":  _MIN_BODY_PCT,
                },
            )

        reason_parts: list[str] = []
        if not is_large_range:
            reason_parts.append(f"range {bar_range_atr:.2f}x ATR within bounds")
        if not is_thin_body:
            reason_parts.append(f"body {body_pct:.1f}% is adequate")

        return ToolResult(
            passed=True,
            reason=f"No liquidity vacuum: {', '.join(reason_parts)}",
            data={
                "bar_range_atr": bar_range_atr,
                "body_pct":      body_pct,
            },
        )

    async def extract_features(self, context) -> FeatureResult:
        m15_ind = context.indicators.get("M15", {})
        liq_data = m15_ind.get("liq_vacuum")

        if liq_data is None:
            return FeatureResult(
                group="volatility",
                features={"candle_quality": 50.0},
                meta={"status": "unavailable"},
            )

        bar_range_atr = float(liq_data.get("bar_range_atr", 0))
        body_pct = float(liq_data.get("body_pct", 100))

        # candle_quality: 100 = healthy (high body%, normal range), 0 = vacuum candle
        range_score = max(0.0, 100 - (bar_range_atr / _MAX_RANGE_ATR) * 50)
        body_score = min(100.0, body_pct / 60 * 100)  # 60% body = perfect
        candle_quality = (range_score + body_score) / 2

        return FeatureResult(
            group="volatility",
            features={"candle_quality": round(min(100.0, candle_quality), 1)},
            reference_thresholds={"max_range_atr": _MAX_RANGE_ATR, "min_body_pct": _MIN_BODY_PCT},
            meta=liq_data,
        )
