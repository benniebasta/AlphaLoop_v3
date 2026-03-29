"""
seedlab/regime_runner.py — Multi-regime backtest runner.

Runs a seed's backtest across each detected regime segment and
collects per-regime + full-data metrics.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from alphaloop.seedlab.metrics import SeedMetrics, extract_metrics
from alphaloop.seedlab.regime_detector import MarketRegime, RegimeDetector, RegimeSegment
from alphaloop.seedlab.seed_generator import StrategySeed

logger = logging.getLogger(__name__)


class RegimeBacktestResult(BaseModel):
    """Result of running one seed across all regimes."""

    seed_hash: str = ""
    seed_name: str = ""
    success: bool = False
    error: str | None = None

    full_metrics: SeedMetrics = Field(default_factory=SeedMetrics)
    regime_metrics: dict[str, SeedMetrics] = Field(default_factory=dict)
    regime_segments: list[RegimeSegment] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class RegimeRunner:
    """
    Runs a backtest function across each detected market regime
    and aggregates per-regime metrics.

    The actual backtest logic is injected via the `backtest_fn` callback.
    """

    def __init__(
        self,
        regime_detector: RegimeDetector | None = None,
        min_regime_bars: int = 100,
    ) -> None:
        self._detector = regime_detector or RegimeDetector()
        self._min_regime_bars = min_regime_bars

    async def run_seed(
        self,
        seed: StrategySeed,
        backtest_fn: Any,
        highs: Any,
        lows: Any,
        closes: Any,
        **backtest_kwargs: Any,
    ) -> RegimeBacktestResult:
        """
        Run a seed through full-data and per-regime backtests.

        Args:
            seed: The strategy seed to evaluate.
            backtest_fn: Async callable (start_idx, end_idx, filters, **kwargs)
                         -> (pnl_usd, pnl_r, outcomes, equity_curve).
            highs, lows, closes: Price arrays for regime detection.
            **backtest_kwargs: Additional args passed to backtest_fn.

        Returns:
            RegimeBacktestResult with full + per-regime metrics.
        """
        import numpy as np

        result = RegimeBacktestResult(
            seed_hash=seed.seed_hash,
            seed_name=seed.name,
        )

        try:
            h = np.asarray(highs, dtype=np.float64)
            l = np.asarray(lows, dtype=np.float64)
            c = np.asarray(closes, dtype=np.float64)

            # Detect regimes
            segments = self._detector.detect_regimes(h, l, c)
            result.regime_segments = segments

            # Full-data backtest
            full_bt = await backtest_fn(
                start_idx=0,
                end_idx=len(c) - 1,
                filters=list(seed.filters),
                **backtest_kwargs,
            )
            pnl_usd, pnl_r, outcomes, equity = full_bt
            result.full_metrics = extract_metrics(
                pnl_usd=pnl_usd,
                pnl_r=pnl_r,
                outcomes=outcomes,
                equity_curve=equity,
                seed_hash=seed.seed_hash,
                regime="full",
            )

            # Per-regime backtests
            for seg in segments:
                if seg.bar_count < self._min_regime_bars:
                    continue

                try:
                    regime_bt = await backtest_fn(
                        start_idx=seg.start_idx,
                        end_idx=seg.end_idx,
                        filters=list(seed.filters),
                        **backtest_kwargs,
                    )
                    r_pnl, r_pnl_r, r_out, r_eq = regime_bt
                    regime_key = f"{seg.regime}_{seg.start_idx}"
                    result.regime_metrics[regime_key] = extract_metrics(
                        pnl_usd=r_pnl,
                        pnl_r=r_pnl_r,
                        outcomes=r_out,
                        equity_curve=r_eq,
                        seed_hash=seed.seed_hash,
                        regime=regime_key,
                    )
                except Exception as exc:
                    logger.warning(
                        "Regime backtest failed for %s/%s: %s",
                        seed.name, seg.regime, exc,
                    )

            result.success = True

        except Exception as exc:
            result.error = str(exc)
            logger.error("Seed backtest failed for %s: %s", seed.name, exc)

        return result
