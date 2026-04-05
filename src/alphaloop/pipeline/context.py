"""Typed runtime context for the v4 pipeline — Phase 4A.

Replaces the loose dict-based context with a validated typed object.
Invariant 7: no context may enter pipeline without validated OHLCV freshness.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Flag, auto
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class DataQualityFlags(Flag):
    """Flags indicating data quality issues detected during context build."""
    CLEAN = 0
    GAP_DETECTED = auto()
    NAN_PRESENT = auto()
    STALE_FEED = auto()
    LOW_BAR_COUNT = auto()
    VALIDATION_WARNING = auto()


@dataclass
class PriceSnapshot:
    """Current price state from broker feed."""
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PipelineContext:
    """Typed context object for the v4 pipeline.

    All pipeline stages (MarketGate, RiskGate, ExecutionGuard, etc.) read
    typed fields instead of dict.get().

    Construct via PipelineContext.build() which validates data freshness
    and raises on critical validation failure (invariant 7).
    """

    symbol: str = ""
    timeframe: str = "M15"
    df: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    latest_price: float = 0.0
    price: PriceSnapshot = field(default_factory=PriceSnapshot)
    median_spread: float = 0.0
    atr_h1: float | None = None
    macro_modifier: float = 1.0
    data_quality: DataQualityFlags = DataQualityFlags.CLEAN
    mode: str = "algo_only"
    strategy_id: str | None = None
    instance_id: str = ""
    indicators: dict[str, dict] = field(default_factory=dict)

    # Session/news context
    session: dict[str, Any] = field(default_factory=dict)
    news: list[dict] = field(default_factory=list)

    # Risk/trade state references (set by caller)
    risk_monitor: Any = None
    open_trades: list = field(default_factory=list)

    # Set by orchestrator after signal generation
    trade_direction: str = ""

    @classmethod
    async def build(
        cls,
        *,
        fetcher,
        symbol: str,
        timeframe: str = "M15",
        executor=None,
        mode: str = "algo_only",
        strategy_id: str | None = None,
        instance_id: str = "",
        risk_monitor=None,
        open_trades: list | None = None,
        session_info: dict | None = None,
        news: list | None = None,
        macro_modifier: float = 1.0,
        indicators: dict | None = None,
    ) -> PipelineContext:
        """Build a validated PipelineContext from data sources.

        Raises DataFetchError on critical validation failure.
        """
        quality = DataQualityFlags.CLEAN

        # Fetch validated OHLCV via fetcher (staleness + integrity checks)
        df = await fetcher.get_ohlcv(timeframe=timeframe)
        if len(df) < 50:
            quality |= DataQualityFlags.LOW_BAR_COUNT
            logger.warning("[context] Low bar count: %d bars for %s", len(df), timeframe)

        if df.isna().any().any():
            quality |= DataQualityFlags.NAN_PRESENT

        # Get current price from executor
        price_snapshot = PriceSnapshot()
        if executor:
            try:
                tick = await executor.get_current_tick()
                if tick:
                    price_snapshot = PriceSnapshot(
                        bid=tick.get("bid", 0.0) if isinstance(tick, dict) else getattr(tick, "bid", 0.0),
                        ask=tick.get("ask", 0.0) if isinstance(tick, dict) else getattr(tick, "ask", 0.0),
                        spread=tick.get("spread", 0.0) if isinstance(tick, dict) else getattr(tick, "spread", 0.0),
                        time=tick.get("time", datetime.now(timezone.utc)) if isinstance(tick, dict) else getattr(tick, "time", datetime.now(timezone.utc)),
                    )
            except Exception as e:
                logger.warning("[context] Failed to get current tick: %s", e)
                quality |= DataQualityFlags.STALE_FEED

        # Check price timestamp freshness
        if price_snapshot.time:
            age_sec = (datetime.now(timezone.utc) - price_snapshot.time).total_seconds()
            if age_sec > 300:
                quality |= DataQualityFlags.STALE_FEED
                logger.warning("[context] Price feed stale: %.0fs old", age_sec)

        latest_price = price_snapshot.bid if price_snapshot.bid > 0 else (
            float(df["close"].iloc[-1]) if len(df) > 0 else 0.0
        )

        # Extract key indicators
        _indicators = indicators or {}
        atr_h1 = None
        median_sprd = 0.0
        if "H1" in _indicators:
            atr_h1 = _indicators["H1"].get("atr")
        if "M15" in _indicators:
            median_sprd = _indicators["M15"].get("median_spread", 0.0)

        return cls(
            symbol=symbol,
            timeframe=timeframe,
            df=df,
            latest_price=latest_price,
            price=price_snapshot,
            median_spread=median_sprd,
            atr_h1=atr_h1,
            macro_modifier=macro_modifier,
            data_quality=quality,
            mode=mode,
            strategy_id=strategy_id,
            instance_id=instance_id,
            indicators=_indicators,
            session=session_info or {},
            news=news or [],
            risk_monitor=risk_monitor,
            open_trades=open_trades or [],
        )

    def get(self, key: str, default=None):
        """Dict-like access for backward compatibility with code that uses context.get()."""
        return getattr(self, key, default)

    def __getitem__(self, key: str):
        """Dict-like access for backward compatibility with context['key']."""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)
