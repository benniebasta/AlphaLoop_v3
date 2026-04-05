"""
scoring/confidence_engine.py
Computes total confidence score from weighted group scores.
"""

from __future__ import annotations

import logging

from alphaloop.scoring.weights import DEFAULT_GROUP_WEIGHTS

logger = logging.getLogger(__name__)


class ConfidenceEngine:
    """
    Weighted aggregation of group scores into a single confidence value (0-100).

    confidence = sum(group_weight * group_score for each group)

    Missing groups default to 50.0 (neutral).
    """

    def __init__(self, group_weights: dict[str, float] | None = None):
        self.weights = group_weights or dict(DEFAULT_GROUP_WEIGHTS)

    def compute(self, group_scores: dict[str, float]) -> float:
        """
        Compute total confidence score (0-100).

        Args:
            group_scores: dict of group_name -> score (0-100)

        Returns:
            Weighted confidence score (0-100).
        """
        total = 0.0
        weight_sum = 0.0

        for group, weight in self.weights.items():
            score = group_scores.get(group, 50.0)  # neutral default
            total += weight * score
            weight_sum += weight

        # Safety: normalize if weights don't sum to 1.0
        if weight_sum > 0 and abs(weight_sum - 1.0) > 0.001:
            total = total / weight_sum

        confidence = round(min(100.0, max(0.0, total)), 2)

        logger.info(
            "[AI] confidence=%.1f weights=%s scores=%s",
            confidence,
            {k: round(v, 2) for k, v in self.weights.items()},
            {k: round(v, 1) for k, v in group_scores.items()},
        )

        return confidence
