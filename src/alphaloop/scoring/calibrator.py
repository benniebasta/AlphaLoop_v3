"""
scoring/calibrator.py

SetupCalibrator — per-setup-type confidence calibration.

Maps raw AI confidence scores to historically-calibrated probabilities
using a simple win-rate multiplier per setup type.

Calibration factor:
  cal_factor = win_rate / 0.5
  - 1.0 = neutral (win rate equals coin flip baseline)
  - > 1.0 = historically profitable setup type
  - < 1.0 = historically weak setup type

Calibrated confidence:
  calibrated = min(raw_confidence * cal_factor, MAX_CONFIDENCE)

At least MIN_SAMPLES trades per setup type are required before
deviating from neutral (1.0 factor). This prevents premature
penalisation or boosting on sparse data.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from statistics import mean

from alphaloop.core.setup_types import normalize_pipeline_setup_type

logger = logging.getLogger(__name__)

MIN_SAMPLES = 20        # trades needed per setup type before applying calibration
MAX_CONFIDENCE = 0.95   # hard ceiling after calibration
BASELINE = 0.5          # neutral win rate (coin flip)

# Known setup types — also accept unknown types with neutral weight
_KNOWN_SETUP_TYPES = frozenset(
    {"pullback", "reversal", "breakout", "continuation", "range_bounce"}
)


class SetupCalibrator:
    """
    Rolling win-rate calibrator per trade setup type.

    Track win rates per setup type (pullback, reversal, etc.) and scale
    AI confidence to account for historically strong or weak setup categories.

    Usage:
        calibrator = SetupCalibrator(window=100)
        calibrator.record("pullback", won=True)
        calibrated = calibrator.calibrate("pullback", raw_confidence=0.75)
    """

    def __init__(self, window: int = 100):
        self._window = window
        self._history: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=window)
        )

    def record(self, setup_type: str, won: bool) -> None:
        """Record a trade outcome for a setup type."""
        self._history[normalize_pipeline_setup_type(setup_type)].append(1 if won else 0)

    def win_rate(self, setup_type: str) -> float:
        """
        Return historical win rate for a setup type.
        Returns 0.5 (neutral) until MIN_SAMPLES have been collected.
        """
        h = self._history[normalize_pipeline_setup_type(setup_type)]
        if len(h) < MIN_SAMPLES:
            return BASELINE
        return round(mean(h), 4)

    def calibration_factor(self, setup_type: str) -> float:
        """
        Return calibration multiplier for a setup type.
        Factor = win_rate / 0.5 → 1.0 at neutral, higher for good setups.
        """
        return self.win_rate(setup_type) / BASELINE

    def calibrate(self, setup_type: str, raw_confidence: float) -> float:
        """
        Apply calibration to a raw AI confidence score.

        Args:
            setup_type: The signal setup type (e.g. "pullback")
            raw_confidence: AI-generated confidence (0.0–1.0)

        Returns:
            Calibrated confidence (0.0–MAX_CONFIDENCE)
        """
        factor = self.calibration_factor(setup_type)
        calibrated = raw_confidence * factor
        result = round(min(calibrated, MAX_CONFIDENCE), 4)

        if factor != 1.0:
            logger.debug(
                "[calibrator] %s: raw=%.3f factor=%.3f → calibrated=%.3f",
                setup_type, raw_confidence, factor, result,
            )
        return result

    def summary(self) -> dict[str, dict]:
        """Return calibration summary for all tracked setup types."""
        out = {}
        for setup_type, history in self._history.items():
            n = len(history)
            wr = mean(history) if n >= MIN_SAMPLES else None
            out[setup_type] = {
                "win_rate": round(wr, 3) if wr is not None else None,
                "samples": n,
                "factor": round(wr / BASELINE, 3) if wr is not None else 1.0,
                "calibrating": n >= MIN_SAMPLES,
            }
        return out

    def seed_from_history(self, setup_outcomes: dict[str, list[bool]]) -> None:
        """
        Seed calibrator from historical trade data at startup.

        Args:
            setup_outcomes: {setup_type: [True, False, True, ...]}
        """
        for setup_type, outcomes in setup_outcomes.items():
            for won in outcomes:
                self.record(setup_type, won)
        logger.info(
            "[calibrator] Seeded %d setup types from history",
            len(setup_outcomes),
        )
