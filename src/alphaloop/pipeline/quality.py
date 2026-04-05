"""
pipeline/quality.py — Stage 4B: Structural quality scoring.

Runs all direction-dependent plugins in extract_features() mode and
aggregates their 0-100 scores into group-level metrics.

NEVER blocks.  Low scores reduce conviction via the ConvictionScorer.
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.pipeline.types import QualityResult
from alphaloop.scoring.feature_aggregator import FeatureAggregator
from alphaloop.scoring.group_scorer import GroupScorer
from alphaloop.scoring.confidence_engine import ConfidenceEngine
from alphaloop.scoring.weights import DEFAULT_GROUP_WEIGHTS
from alphaloop.tools.base import BaseTool, FeatureResult

logger = logging.getLogger(__name__)

# Contradiction threshold — tools scoring below this are logged
_CONTRADICTION_THRESHOLD = 25.0


class StructuralQuality:
    """
    Runs all provided tools in feature-extraction mode and scores them.

    Uses the existing FeatureAggregator → GroupScorer → ConfidenceEngine
    pipeline from the scoring/ module.
    """

    def __init__(
        self,
        tools: list[BaseTool] | None = None,
        *,
        tracker=None,
    ):
        self._tools = tools or []
        self._aggregator = FeatureAggregator()
        self._scorer = GroupScorer()
        self._tracker = tracker

    async def evaluate(
        self,
        context,
        weights: dict[str, float] | None = None,
    ) -> QualityResult:
        """
        Extract features from all tools and compute quality scores.

        Args:
            context: MarketContext with trade_direction set
            weights: Group weights (regime-adjusted). Falls back to defaults.
        """
        effective_weights = weights or dict(DEFAULT_GROUP_WEIGHTS)

        # --- Run all tools in feature mode ---
        results: list[FeatureResult] = []
        for tool in self._tools:
            try:
                fr = await tool.timed_extract_features(context)
                if fr is not None:
                    results.append(fr)
            except Exception as exc:
                logger.warning(
                    "[Quality] %s extract_features() failed: %s",
                    getattr(tool, "name", "unknown"),
                    exc,
                )

        # --- Aggregate by group ---
        grouped = self._aggregator.aggregate(results)

        # --- Score each group ---
        group_scores = self._scorer.score_all_groups(
            grouped, tracker=self._tracker
        )

        # --- Compute overall weighted score ---
        engine = ConfidenceEngine(effective_weights)
        overall = engine.compute(group_scores)

        # --- Identify contradictions and extremes ---
        tool_scores: dict[str, float] = {}
        contradictions: list[str] = []
        max_score = 0.0

        for fr in results:
            if fr.features:
                avg = sum(fr.features.values()) / len(fr.features)
                tool_scores[fr.tool_name] = round(avg, 1)
                max_score = max(max_score, avg)
                if avg < _CONTRADICTION_THRESHOLD:
                    contradictions.append(fr.tool_name)

        low_count = len(contradictions)

        result = QualityResult(
            tool_scores=tool_scores,
            group_scores={k: round(v, 1) for k, v in group_scores.items()},
            overall_score=round(overall, 1),
            contradictions=contradictions,
            low_score_count=low_count,
            max_score=round(max_score, 1),
        )

        logger.info(
            "[Quality] overall=%.1f | groups=%s | contradictions=%d (%s)",
            overall,
            {k: f"{v:.0f}" for k, v in group_scores.items()},
            low_count,
            contradictions,
        )

        return result
