"""
scoring/decision.py
Maps confidence score + direction to a trade decision (BUY/SELL/HOLD).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from alphaloop.scoring.weights import DEFAULT_CONFIDENCE_THRESHOLDS

logger = logging.getLogger(__name__)


class ScoringDecision(BaseModel):
    """Output of the scoring engine decision process."""

    direction: str = "HOLD"        # "BUY" | "SELL" | "HOLD"
    confidence: float = 0.0        # 0.0 - 1.0 (for TradeSignal compatibility)
    raw_confidence: float = 0.0    # 0 - 100 (internal scale)
    size_scalar: float = 0.0       # 0.0 - 1.0 (multiplied into position size)
    group_scores: dict[str, float] = Field(default_factory=dict)
    reasoning: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DecisionMaker:
    """
    Converts confidence score into a trade decision.

    Thresholds (configurable per strategy):
      confidence >= strong_entry (75) -> full size (1.0x)
      confidence >= min_entry (60)    -> reduced size (0.6x)
      confidence < min_entry          -> HOLD (no trade)
    """

    def __init__(self, thresholds: dict[str, float] | None = None):
        t = thresholds or dict(DEFAULT_CONFIDENCE_THRESHOLDS)
        self.strong_entry = t.get("strong_entry", 75.0)
        self.min_entry = t.get("min_entry", 60.0)

    def decide(
        self,
        confidence: float,
        direction: str,
        group_scores: dict[str, float],
    ) -> ScoringDecision:
        """
        Map confidence + proposed direction to a decision.

        Args:
            confidence: 0-100 score from ConfidenceEngine
            direction: "BUY" or "SELL" from the signal engine
            group_scores: per-group scores for logging/debugging

        Returns:
            ScoringDecision with direction, confidence (0-1), and size_scalar.
        """
        if confidence >= self.strong_entry:
            size_scalar = 1.0
            final_direction = direction.upper()
            reasoning = (
                f"Strong signal: confidence {confidence:.1f} >= {self.strong_entry} "
                f"-> full size {final_direction}"
            )
        elif confidence >= self.min_entry:
            size_scalar = 0.6
            final_direction = direction.upper()
            reasoning = (
                f"Moderate signal: confidence {confidence:.1f} >= {self.min_entry} "
                f"-> reduced size (0.6x) {final_direction}"
            )
        else:
            size_scalar = 0.0
            final_direction = "HOLD"
            reasoning = (
                f"Weak signal: confidence {confidence:.1f} < {self.min_entry} "
                f"-> HOLD (no trade)"
            )

        decision = ScoringDecision(
            direction=final_direction,
            confidence=round(confidence / 100.0, 4),  # normalize to 0-1
            raw_confidence=round(confidence, 2),
            size_scalar=round(size_scalar, 2),
            group_scores=group_scores,
            reasoning=reasoning,
        )

        logger.info(
            "[DECISION] %s conf=%.1f size=%.2f — %s",
            decision.direction, confidence, size_scalar, reasoning,
        )

        return decision
