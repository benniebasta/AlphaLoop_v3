"""
scoring/group_scorer.py
Computes a single 0-100 score per scoring group from FeatureResult lists.

Supports optional win-rate weighting via ToolPerformanceTracker.
When a tracker is provided, each plugin's score is weighted by its
historical win rate (information coefficient proxy). Tools with higher
historical accuracy contribute more to the group score.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from alphaloop.tools.base import FeatureResult

if TYPE_CHECKING:
    from alphaloop.scoring.tool_tracker import ToolPerformanceTracker

logger = logging.getLogger(__name__)

_NEUTRAL = 50.0


class GroupScorer:
    """
    Scores each group from FeatureResult lists.

    Without tracker: simple arithmetic mean of plugin scores (original behaviour).
    With tracker: win-rate–weighted mean — tools with higher historical accuracy
    contribute proportionally more to the group score.
    """

    def score_group(
        self,
        group_results: list[FeatureResult],
        tracker: "ToolPerformanceTracker | None" = None,
    ) -> float:
        """
        Compute group score (0-100) from a list of FeatureResults.

        Args:
            group_results: FeatureResult objects for this group.
            tracker: Optional ToolPerformanceTracker for win-rate weighting.
                     When None, falls back to equal weighting.

        Returns 50.0 (neutral) if no results or no features.
        """
        if not group_results:
            return _NEUTRAL

        weighted_sum = 0.0
        weight_sum = 0.0

        for result in group_results:
            if not result.features:
                continue
            values = list(result.features.values())
            plugin_score = sum(values) / len(values)

            # Win-rate weight: tracker provides 0.0–1.0 multiplier
            # Falls back to 0.5 (neutral) if tracker absent or insufficient data
            if tracker is not None:
                win_rate = tracker.win_rate(result.tool_name)
            else:
                win_rate = 0.5

            weighted_sum += plugin_score * win_rate
            weight_sum += win_rate

        if weight_sum == 0:
            return _NEUTRAL

        group_score = weighted_sum / weight_sum
        return round(min(100.0, max(0.0, group_score)), 2)

    def score_all_groups(
        self,
        grouped: dict[str, list[FeatureResult]],
        tracker: "ToolPerformanceTracker | None" = None,
    ) -> dict[str, float]:
        """
        Score every group. Returns dict of group_name -> score (0-100).

        Args:
            grouped: Mapping of group_name -> list[FeatureResult].
            tracker: Optional ToolPerformanceTracker for win-rate weighting.
        """
        scores: dict[str, float] = {}
        for group_name, results in grouped.items():
            score = self.score_group(results, tracker=tracker)
            scores[group_name] = score
            logger.info("[GROUP] %s=%.1f (%d plugins)", group_name, score, len(results))
        return scores
