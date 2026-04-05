"""
scoring/ — AI confidence scoring engine.

Converts normalized plugin features (0-100) into a total confidence score
via weighted group aggregation. Used in ALGO_AI signal mode.
"""

from alphaloop.scoring.weights import DEFAULT_GROUP_WEIGHTS, load_weights
from alphaloop.scoring.feature_aggregator import FeatureAggregator
from alphaloop.scoring.group_scorer import GroupScorer
from alphaloop.scoring.confidence_engine import ConfidenceEngine
from alphaloop.scoring.decision import DecisionMaker, ScoringDecision

__all__ = [
    "DEFAULT_GROUP_WEIGHTS",
    "load_weights",
    "FeatureAggregator",
    "GroupScorer",
    "ConfidenceEngine",
    "DecisionMaker",
    "ScoringDecision",
]
