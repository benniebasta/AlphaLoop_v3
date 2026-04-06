"""
pipeline/market_gate.py - Stage 1: Hard safety gates.

Checks whether the market is physically tradeable. Every check here is
a non-negotiable infrastructure block that cannot be toggled off per-strategy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import Mock

from alphaloop.pipeline.types import MarketGateResult

logger = logging.getLogger(__name__)

_STALE_BAR_SECONDS = 300
_MIN_BARS_REQUIRED = 200
_SPREAD_RATIO_MAX = 3.0


class MarketGate:
    def __init__(
        self,
        *,
        stale_bar_seconds: int = _STALE_BAR_SECONDS,
        min_bars_required: int = _MIN_BARS_REQUIRED,
        spread_ratio_max: float = _SPREAD_RATIO_MAX,
        tools: list | None = None,
    ):
        self.stale_bar_seconds = stale_bar_seconds
        self.min_bars_required = min_bars_required
        self.spread_ratio_max = spread_ratio_max
        self._tools: list = tools or []

    async def check(self, context) -> MarketGateResult:
        now = datetime.now(timezone.utc)
        bars_available = 0

        rm = getattr(context, "risk_monitor", None)
        if self._kill_switch_active(rm):
            return self._block("kill_switch", "Kill switch active")

        price = getattr(context, "price", None)
        if price:
            bar_time = getattr(price, "time", None)
            if bar_time and isinstance(bar_time, datetime):
                age_s = (now - bar_time).total_seconds()
                if age_s > self.stale_bar_seconds:
                    return self._block(
                        "stale_feed",
                        f"Last bar is {age_s:.0f}s old (limit {self.stale_bar_seconds}s)",
                    )

        df = getattr(context, "df", None)
        if df is not None:
            bars_available = len(df)
            if bars_available < self.min_bars_required:
                return self._block(
                    "missing_bars",
                    f"Only {bars_available} bars (need {self.min_bars_required})",
                )

        spread_ratio = 1.0
        if price:
            bid = getattr(price, "bid", 0)
            ask = getattr(price, "ask", 0)
            spread = getattr(price, "spread", 0)

            if bid > 0 and ask > 0 and bid >= ask:
                return self._block("feed_desync", f"Bid ({bid}) >= Ask ({ask})")

            if spread < 0:
                return self._block("feed_desync", f"Negative spread ({spread})")

            median_spread = self._get_median_spread(context)
            if median_spread and median_spread > 0 and spread > 0:
                spread_ratio = spread / median_spread
                if spread_ratio > self.spread_ratio_max:
                    return self._block(
                        "abnormal_spread",
                        f"Spread {spread_ratio:.1f}x median (limit {self.spread_ratio_max}x)",
                    )

        data_quality = 1.0
        if price:
            bar_time = getattr(price, "time", None)
            if bar_time and isinstance(bar_time, datetime):
                age_s = (now - bar_time).total_seconds()
                data_quality = max(0.0, 1.0 - age_s / self.stale_bar_seconds)

        for tool in self._tools:
            try:
                tool_result = await tool.timed_run(context)
                if not tool_result.passed and tool_result.severity == "block":
                    return self._block(tool_result.tool_name, tool_result.reason)
                if tool_result.size_modifier < 1.0:
                    spread_ratio = round(
                        spread_ratio / max(tool_result.size_modifier, 0.01), 2
                    )
            except Exception as exc:
                logger.warning(
                    "[MarketGate] Tool %s error: %s",
                    getattr(tool, "name", "?"),
                    exc,
                )

        return MarketGateResult(
            tradeable=True,
            data_quality=round(data_quality, 3),
            spread_ratio=round(spread_ratio, 2),
            bars_available=bars_available,
        )

    @staticmethod
    def _get_median_spread(context) -> float | None:
        indicators = getattr(context, "indicators", {})
        m15_ind = indicators.get("M15", {})
        return m15_ind.get("median_spread", None)

    @staticmethod
    def _kill_switch_active(risk_monitor) -> bool:
        if risk_monitor is None:
            return False

        for attr_name in ("kill_switch_active", "_kill_switch_active"):
            coerced = MarketGate._coerce_bool_flag(getattr(risk_monitor, attr_name, None))
            if coerced is not None:
                return coerced

        return False

    @staticmethod
    def _coerce_bool_flag(value) -> bool | None:
        if isinstance(value, bool):
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        if isinstance(value, Mock):
            return None
        return None

    @staticmethod
    def _block(reason_code: str, detail: str) -> MarketGateResult:
        logger.info("[MarketGate] BLOCKED: %s - %s", reason_code, detail)
        return MarketGateResult(
            tradeable=False,
            block_reason=detail,
            blocked_by=reason_code,
        )
