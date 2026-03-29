"""
seedlab/regime_detector.py — Market regime classification.

Classifies market data into regimes: trending, ranging, volatile, dead.
Uses ATR-based volatility and directional movement for classification.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import numpy as np
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class MarketRegime(StrEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    DEAD = "dead"


class RegimeSegment(BaseModel):
    """A contiguous period classified as a single regime."""

    regime: MarketRegime
    start_idx: int
    end_idx: int
    bar_count: int
    avg_atr: float = 0.0
    directional_strength: float = 0.0

    model_config = {"frozen": True}


class RegimeDetector:
    """
    Classifies OHLC bars into market regimes.

    Classification logic:
    - TRENDING: strong directional movement (ADX > threshold or price slope)
    - RANGING: low volatility, mean-reverting price action
    - VOLATILE: high ATR relative to recent history
    - DEAD: extremely low ATR and volume
    """

    def __init__(
        self,
        atr_period: int = 14,
        lookback: int = 50,
        volatility_high_mult: float = 1.5,
        volatility_dead_mult: float = 0.3,
        trend_slope_threshold: float = 0.001,
        min_segment_bars: int = 50,
    ) -> None:
        self.atr_period = atr_period
        self.lookback = lookback
        self.volatility_high_mult = volatility_high_mult
        self.volatility_dead_mult = volatility_dead_mult
        self.trend_slope_threshold = trend_slope_threshold
        self.min_segment_bars = min_segment_bars

    def detect_regimes(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
    ) -> list[RegimeSegment]:
        """
        Classify each bar and merge into contiguous segments.

        Args:
            highs: Array of high prices.
            lows: Array of low prices.
            closes: Array of close prices.

        Returns:
            List of RegimeSegment objects.
        """
        n = len(closes)
        if n < self.lookback + self.atr_period:
            logger.warning("Insufficient data for regime detection (%d bars)", n)
            return [RegimeSegment(
                regime=MarketRegime.RANGING,
                start_idx=0, end_idx=n - 1, bar_count=n,
            )]

        # Compute ATR
        atr = self._compute_atr(highs, lows, closes)

        # Compute rolling median ATR for relative comparison
        median_atr = self._rolling_median(atr, self.lookback)

        # Compute price slope (normalized by price level)
        slopes = self._compute_slope(closes, window=self.lookback)

        # Classify each bar
        regimes: list[MarketRegime] = []
        for i in range(n):
            if i < self.lookback:
                regimes.append(MarketRegime.RANGING)
                continue

            atr_ratio = atr[i] / median_atr[i] if median_atr[i] > 0 else 1.0
            slope = abs(slopes[i]) if i < len(slopes) else 0.0

            if atr_ratio < self.volatility_dead_mult:
                regimes.append(MarketRegime.DEAD)
            elif atr_ratio > self.volatility_high_mult:
                regimes.append(MarketRegime.VOLATILE)
            elif slope > self.trend_slope_threshold:
                regimes.append(MarketRegime.TRENDING)
            else:
                regimes.append(MarketRegime.RANGING)

        # Merge into segments
        return self._merge_segments(regimes, atr)

    def _merge_segments(
        self, regimes: list[MarketRegime], atr: np.ndarray
    ) -> list[RegimeSegment]:
        """Merge consecutive same-regime bars into segments."""
        if not regimes:
            return []

        segments: list[RegimeSegment] = []
        start = 0
        current = regimes[0]

        for i in range(1, len(regimes)):
            if regimes[i] != current:
                bar_count = i - start
                if bar_count >= self.min_segment_bars or not segments:
                    segments.append(RegimeSegment(
                        regime=current,
                        start_idx=start,
                        end_idx=i - 1,
                        bar_count=bar_count,
                        avg_atr=round(float(np.mean(atr[start:i])), 4),
                    ))
                elif segments:
                    # Merge small segments into the previous one
                    prev = segments[-1]
                    segments[-1] = RegimeSegment(
                        regime=prev.regime,
                        start_idx=prev.start_idx,
                        end_idx=i - 1,
                        bar_count=prev.bar_count + bar_count,
                        avg_atr=round(float(np.mean(atr[prev.start_idx:i])), 4),
                    )
                start = i
                current = regimes[i]

        # Final segment
        bar_count = len(regimes) - start
        segments.append(RegimeSegment(
            regime=current,
            start_idx=start,
            end_idx=len(regimes) - 1,
            bar_count=bar_count,
            avg_atr=round(float(np.mean(atr[start:])), 4),
        ))

        return segments

    @staticmethod
    def _compute_atr(
        highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
    ) -> np.ndarray:
        """Compute Average True Range."""
        n = len(closes)
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        atr = np.zeros(n)
        atr[:period] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    @staticmethod
    def _rolling_median(arr: np.ndarray, window: int) -> np.ndarray:
        """Simple rolling median."""
        n = len(arr)
        result = np.zeros(n)
        for i in range(n):
            start = max(0, i - window + 1)
            result[i] = np.median(arr[start : i + 1])
        return result

    @staticmethod
    def _compute_slope(closes: np.ndarray, window: int) -> np.ndarray:
        """Normalized price slope over a rolling window."""
        n = len(closes)
        slopes = np.zeros(n)
        for i in range(window, n):
            segment = closes[i - window : i + 1]
            if segment[0] > 0:
                slopes[i] = (segment[-1] - segment[0]) / (segment[0] * window)
        return slopes
