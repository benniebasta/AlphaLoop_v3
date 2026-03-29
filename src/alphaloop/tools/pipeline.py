"""
tools/pipeline.py
Async FilterPipeline — runs tools in sequence with short-circuit on block.

Pipeline order follows the trading decision flow:
  1. session_filter   — Is this a tradeable session?
  2. news_filter      — High-impact news blackout?
  3. volatility_filter — ATR within range?
  4. dxy_filter       — USD direction conflict?
  5. sentiment_filter  — Macro sentiment conflict?
  6. risk_filter      — Risk limits allow new trade?
  7+ guards          — BOS, FVG, VWAP, correlation checks

Short-circuit: pipeline stops on first block (passed=False with severity="block").
"""

from __future__ import annotations

import logging
from typing import Optional

from alphaloop.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class FilterPipeline:
    """
    Async pipeline that runs filter tools sequentially.

    - Short-circuits on first block (severity="block" and passed=False)
    - Accumulates size_modifier as product of all tool modifiers
    - Tracks last non-neutral bias
    - Crash in any tool results in fail-safe block

    Args:
        tools: List of BaseTool instances to run in order
        short_circuit: Stop on first block (default True)
        size_floor: Minimum aggregate size_modifier — below this, block trade
    """

    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        short_circuit: bool = True,
        size_floor: float = 0.20,
    ) -> None:
        self._tools = tools or []
        self.short_circuit = short_circuit
        self.size_floor = size_floor
        self._crash_counts: dict[str, int] = {}
        self._CRASH_ALERT_THRESHOLD = 3

    async def run(self, context) -> dict:
        """
        Run all tools against the given MarketContext.

        Returns a summary dict:
        {
            "allow_trade":   bool,
            "block_reason":  str | None,
            "blocked_by":    str | None,
            "size_modifier": float,
            "bias":          str,
            "results":       list[dict],
        }
        """
        results: list[ToolResult] = []
        blocked_by: Optional[str] = None
        block_reason: Optional[str] = None
        combined_size = 1.0
        last_bias = "neutral"

        for tool in self._tools:
            try:
                result = await tool.timed_run(context)
                self._crash_counts[tool.name] = 0
                logger.info(
                    f"[filter] {result.tool_name}: passed={result.passed} "
                    f"bias={result.bias} size_mod={result.size_modifier:.2f} "
                    f"({result.latency_ms}ms) — {result.reason}"
                )
            except Exception as e:
                logger.exception(f"[pipeline] Tool {tool.name} crashed — fail-safe block: {e}")
                crash_count = self._crash_counts.get(tool.name, 0) + 1
                self._crash_counts[tool.name] = crash_count
                if crash_count == self._CRASH_ALERT_THRESHOLD:
                    logger.critical(
                        f"[pipeline] ALERT: {tool.name} has crashed "
                        f"{crash_count} consecutive times"
                    )
                result = ToolResult(
                    tool_name=tool.name,
                    passed=False,
                    reason=f"Tool error (fail-safe block): {e}",
                    severity="block",
                )

            results.append(result)

            if result.bias != "neutral":
                last_bias = result.bias

            combined_size *= result.size_modifier

            if not result.passed and result.severity == "block":
                blocked_by = result.tool_name
                block_reason = result.reason
                if self.short_circuit:
                    logger.info(f"[pipeline] BLOCKED by {blocked_by}: {block_reason}")
                    break

        # Clamp aggregate size_modifier
        combined_size = max(0.0, min(1.0, combined_size))

        allow = blocked_by is None

        # Block if combined modifier below floor
        if allow and combined_size < self.size_floor:
            allow = False
            blocked_by = "pipeline_size_floor"
            block_reason = (
                f"Combined size_modifier {combined_size:.3f} below floor "
                f"{self.size_floor} — signal quality too degraded"
            )
            logger.warning(f"[pipeline] BLOCKED: {block_reason}")

        if allow:
            logger.info(
                f"[pipeline] ALLOWED | size_mod={combined_size:.2f} | bias={last_bias}"
            )

        return {
            "allow_trade": allow,
            "block_reason": block_reason,
            "blocked_by": blocked_by,
            "size_modifier": round(combined_size, 4),
            "bias": last_bias,
            "results": [r.model_dump() for r in results],
        }

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get a specific tool by name."""
        return next((t for t in self._tools if t.name == name), None)

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)
