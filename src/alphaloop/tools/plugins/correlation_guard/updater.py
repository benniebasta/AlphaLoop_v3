"""
tools/plugins/correlation_guard/updater.py — Dynamic correlation matrix updater.

Computes pairwise Pearson correlations from recent daily OHLCV data
and persists the result to the DB settings table (key: correlation_matrix).

The CorrelationGuard tool reads from settings on each call, falling back
to the static hardcoded map if no computed matrix is present.

Usage:
    updater = CorrelationMatrixUpdater()
    await updater.update_and_persist(settings_service)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SETTINGS_KEY = "correlation_matrix"

# Default symbols to compute correlation for
_DEFAULT_SYMBOLS = [
    "XAUUSD", "EURUSD", "GBPUSD", "BTCUSD", "DXY",
]


class CorrelationMatrixUpdater:
    """
    Computes pairwise Pearson correlations from daily close prices.

    Parameters
    ----------
    symbols : list[str] | None
        Symbols to include. Defaults to a set of commonly traded pairs.
    lookback_days : int
        Number of daily bars to use. Default 60.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        lookback_days: int = 60,
    ) -> None:
        self._symbols = symbols or _DEFAULT_SYMBOLS
        self._lookback = lookback_days

    def compute(self) -> dict[str, float]:
        """
        Fetch daily closes and compute pairwise correlations.

        Returns
        -------
        dict with keys like "XAUUSD|EURUSD" -> float correlation value.
        Empty dict if data unavailable.
        """
        try:
            import yfinance as yf
            import pandas as pd
            from alphaloop.data.yf_catalog import get_yf_ticker
        except ImportError:
            logger.warning("[corr_updater] yfinance or pandas not available")
            return {}

        try:
            # Fetch daily closes for all symbols
            tickers_map: dict[str, str] = {}
            for sym in self._symbols:
                try:
                    yf_sym = get_yf_ticker(sym) or sym
                    tickers_map[sym] = yf_sym
                except Exception:
                    tickers_map[sym] = sym

            # Download in batch for efficiency
            yf_tickers = list(tickers_map.values())
            raw = yf.download(
                yf_tickers,
                period=f"{self._lookback + 10}d",
                interval="1d",
                progress=False,
                auto_adjust=True,
            )

            if raw is None or raw.empty:
                logger.warning("[corr_updater] No data returned from yfinance")
                return {}

            # Extract Close prices
            if hasattr(raw.columns, "levels") and raw.columns.nlevels > 1:
                closes = raw["Close"]
            else:
                closes = raw[["Close"]] if "Close" in raw.columns else raw

            # Rename columns back to AlphaLoop symbols
            rev_map = {v: k for k, v in tickers_map.items()}
            closes = closes.rename(columns=rev_map)

            # Drop columns with too many NaN
            closes = closes.dropna(axis=1, thresh=int(self._lookback * 0.7))

            if closes.shape[1] < 2:
                logger.warning("[corr_updater] Not enough symbols with data")
                return {}

            # Compute pairwise Pearson correlations on daily returns
            returns = closes.pct_change().dropna()
            corr_matrix = returns.corr()

            # Flatten to dict: "SYM_A|SYM_B" -> float (canonical order: A < B)
            result: dict[str, float] = {}
            syms = list(corr_matrix.columns)
            for i, sa in enumerate(syms):
                for sb in syms[i + 1:]:
                    val = corr_matrix.loc[sa, sb]
                    if val == val:  # filter NaN
                        key = f"{sa}|{sb}" if sa <= sb else f"{sb}|{sa}"
                        result[key] = round(float(val), 4)

            logger.info(
                "[corr_updater] Computed %d correlations from %d symbols (%d days)",
                len(result), len(syms), len(returns),
            )
            return result

        except Exception as e:
            logger.warning("[corr_updater] Computation failed: %s", e)
            return {}

    async def update_and_persist(self, settings_service) -> bool:
        """
        Compute the matrix, save to DB settings, and update the in-memory cache.

        Parameters
        ----------
        settings_service : SettingsService
            Used to persist the matrix.

        Returns
        -------
        bool
            True if successfully saved.
        """
        try:
            matrix = self.compute()
            if not matrix:
                logger.warning("[corr_updater] Empty matrix — not persisting")
                return False

            await settings_service.set(_SETTINGS_KEY, json.dumps(matrix))
            logger.info("[corr_updater] Correlation matrix persisted (%d pairs)", len(matrix))

            # Also update the in-memory cache in the correlation guard tool
            # so that the new matrix is used immediately without a process restart.
            try:
                from alphaloop.tools.plugins.correlation_guard import tool as _ct
                parsed: dict[tuple[str, str], float] = {}
                for key_str, val in matrix.items():
                    parts = key_str.split("|")
                    if len(parts) == 2:
                        parsed[tuple(parts)] = float(val)  # type: ignore[assignment]
                _ct._DYNAMIC_MATRIX = parsed
                logger.debug("[corr_updater] In-memory matrix updated (%d pairs)", len(parsed))
            except Exception as cache_err:
                logger.debug("[corr_updater] Could not update in-memory cache: %s", cache_err)

            return True
        except Exception as e:
            logger.error("[corr_updater] Persist failed: %s", e)
            return False
