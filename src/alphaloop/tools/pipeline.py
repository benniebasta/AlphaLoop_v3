"""
tools/pipeline.py
FeaturePipeline — runs all tools via extract_features() for algo_ai scoring.

The v4 institutional pipeline (PipelineOrchestrator) in
``alphaloop.pipeline.orchestrator`` is the primary 8-stage live-trading path.
This module only provides FeaturePipeline for algo_ai feature extraction.
"""

from __future__ import annotations

import asyncio
import logging

from alphaloop.tools.base import BaseTool, FeatureResult

_TOOL_TIMEOUT_SEC = 5.0

logger = logging.getLogger(__name__)


class FeaturePipeline:
    """
    Async pipeline that runs ALL tools via extract_features() — no short-circuit.

    Unlike the v4 PipelineOrchestrator (short-circuits on block), this pipeline:
      - Calls timed_extract_features() on every tool
      - Skips tools that return None (no extract_features impl)
      - Catches per-tool exceptions without crashing the pipeline
      - Returns list[FeatureResult] for the scoring engine

    Used exclusively in algo_ai signal mode.
    """

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools = tools or []

    async def run(self, context) -> list[FeatureResult]:
        """
        Run all tools' extract_features() and collect results.

        Returns list of FeatureResult (only from tools that produced one).
        """
        results: list[FeatureResult] = []

        for tool in self._tools:
            try:
                result = await asyncio.wait_for(
                    tool.timed_extract_features(context), timeout=_TOOL_TIMEOUT_SEC
                )
                if result is not None:
                    logger.info(
                        "[FEATURE] %s: %s (%.1fms)",
                        result.tool_name,
                        " ".join(f"{k}={v:.1f}" for k, v in result.features.items()),
                        result.latency_ms,
                    )
                    results.append(result)
            except asyncio.TimeoutError:
                logger.warning(
                    "[feature-pipeline] %s timed out after %.1fs — skipping",
                    tool.name, _TOOL_TIMEOUT_SEC,
                )
            except Exception as e:
                logger.warning(
                    "[feature-pipeline] %s crashed — skipping: %s",
                    tool.name, e,
                )

        logger.info(
            "[feature-pipeline] Collected %d feature results from %d tools",
            len(results), len(self._tools),
        )
        return results

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)
