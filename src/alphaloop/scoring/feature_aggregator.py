"""
scoring/feature_aggregator.py
Collects FeatureResult objects and groups them by scoring category.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from alphaloop.tools.base import FeatureResult
from alphaloop.scoring.weights import SCORING_GROUPS

logger = logging.getLogger(__name__)


class FeatureAggregator:
    """
    Groups FeatureResult objects by their scoring category.

    Unknown groups are logged and placed in a special '_unassigned' bucket
    (ignored by the scorer). Empty groups are included with empty lists
    so the scorer can apply neutral defaults.
    """

    def aggregate(self, results: list[FeatureResult]) -> dict[str, list[FeatureResult]]:
        """
        Group FeatureResult list by .group field.

        Returns dict with all known group keys (even if empty).
        """
        grouped: dict[str, list[FeatureResult]] = {g: [] for g in SCORING_GROUPS}

        for result in results:
            group = result.group
            if group in grouped:
                grouped[group].append(result)
            else:
                logger.warning(
                    "[aggregator] Unknown group '%s' from tool '%s' — skipping",
                    group, result.tool_name,
                )

        for group, items in grouped.items():
            logger.debug(
                "[aggregator] %s: %d plugins (%s)",
                group, len(items),
                ", ".join(r.tool_name for r in items),
            )

        return grouped

    def flatten_features(self, results: list[FeatureResult]) -> dict[str, float]:
        """
        Flatten all features from all results into a single dict.

        Keys are prefixed with tool_name to avoid collisions:
          e.g. "ema200_filter.ema200_position": 85.0
        """
        flat: dict[str, float] = {}
        for result in results:
            prefix = result.tool_name or "unknown"
            for key, value in result.features.items():
                flat[f"{prefix}.{key}"] = value
        return flat
