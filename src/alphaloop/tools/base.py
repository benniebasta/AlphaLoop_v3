"""
tools/base.py
Standard interface for all AlphaLoop filter tools.

Every tool must return a ToolResult. The pipeline uses this to
decide whether to allow/block a trade and by how much to scale size.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class FeatureResult(BaseModel):
    """
    Standard output from every feature plugin in ALGO_AI mode.

    All feature values are normalized 0-100 where:
      100 = maximally favorable for taking the trade
        0 = maximally unfavorable (would be blocked in binary mode)
       50 = neutral / unavailable
    """

    tool_name: str = ""
    group: str = ""  # "trend" | "momentum" | "structure" | "volume" | "volatility"
    features: dict[str, float] = Field(default_factory=dict)
    reference_thresholds: dict[str, float] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float = 0.0

    def model_post_init(self, __context: Any) -> None:
        # Clamp all feature values to [0, 100]
        for k, v in self.features.items():
            if v > 100.0:
                self.features[k] = 100.0
            elif v < 0.0:
                self.features[k] = 0.0


class ToolResult(BaseModel):
    """
    Standard output format for every filter tool.

    Fields:
        tool_name     — Name of the tool that produced this result
        passed        — True = allow, False = block
        reason        — Human-readable explanation
        data          — Extra data for debugging / research
        timestamp     — When the result was produced
        severity      — "block" | "warn" | "info" — block causes short-circuit
        size_modifier — Multiply position size by this (0.0-1.0); 1.0 = no change
        bias          — "bullish" | "bearish" | "neutral"
        latency_ms    — How long the tool took to run
    """

    tool_name: str = ""
    passed: bool = True
    reason: str = ""
    data: Optional[dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: str = "info"  # "block" | "warn" | "info"
    size_modifier: float = 1.0
    bias: str = "neutral"
    latency_ms: float = 0.0

    def model_post_init(self, __context: Any) -> None:
        # Clamp size_modifier to [0.0, 1.0]
        if self.size_modifier > 1.0:
            self.size_modifier = 1.0
        elif self.size_modifier < 0.0:
            self.size_modifier = 0.0


class BaseTool(ABC):
    """
    Abstract base class for all filter/guard tools.

    Subclasses must implement async run(context).
    The pipeline calls run() and wraps timing automatically.

    Example:
        class MyFilter(BaseTool):
            name = "my_filter"
            description = "Checks something important"

            async def run(self, context: MarketContext) -> ToolResult:
                return ToolResult(passed=True, reason="All good")
    """

    name: str = "base_tool"
    description: str = ""

    @abstractmethod
    async def run(self, context) -> ToolResult:
        """
        Execute the tool against the given MarketContext.

        Args:
            context: MarketContext instance with all market data

        Returns:
            ToolResult with passed/blocked status, reason, and optional data
        """
        ...

    requires_direction: bool = False
    """Set True on direction-dependent tools to auto-skip when direction is unknown."""

    async def timed_run(self, context) -> ToolResult:
        """Run the tool and record latency + tool_name."""
        t0 = time.monotonic()
        if self.requires_direction and not getattr(context, "trade_direction", ""):
            result = ToolResult(
                passed=True,
                reason="Direction unknown at filter stage — skipping",
                severity="info",
            )
        else:
            result = await self.run(context)
        result.latency_ms = round((time.monotonic() - t0) * 1000, 1)
        result.tool_name = self.name
        return result

    async def extract_features(self, context) -> FeatureResult | None:
        """
        Extract normalized features for ALGO_AI scoring mode.

        Override in subclasses to provide 0-100 feature values.
        Returns None by default — FeaturePipeline skips tools that return None.
        """
        return None

    async def timed_extract_features(self, context) -> FeatureResult | None:
        """Run extract_features and record latency + tool_name."""
        t0 = time.monotonic()
        result = await self.extract_features(context)
        if result is not None:
            result.latency_ms = round((time.monotonic() - t0) * 1000, 1)
            result.tool_name = self.name
        return result
