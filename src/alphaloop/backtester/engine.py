"""
backtester/engine.py — Async backtest engine with walk-forward support.

Provides BacktestEngine for running rule-based backtests on OHLC data,
producing BacktestResult with trades, equity curve, and summary metrics.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.core.events import EventBus
from alphaloop.core.types import BacktestState, TradeDirection, TradeOutcome
from alphaloop.db.repositories.backtest_repo import BacktestRepository

logger = logging.getLogger(__name__)


class BacktestTrade(BaseModel):
    """Single trade produced during a backtest run."""

    bar_index: int = 0
    direction: TradeDirection = TradeDirection.BUY
    entry: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    lots: float = 0.0
    open_time: datetime | None = None
    close_time: datetime | None = None
    close_price: float | None = None
    pnl_r: float | None = None
    pnl_usd: float | None = None
    outcome: TradeOutcome = TradeOutcome.OPEN
    setup_type: str = ""
    confidence: float = 0.0
    session: str = ""
    filters_used: list[str] = Field(default_factory=list)


class BacktestResult(BaseModel):
    """Complete result of a backtest run."""

    run_id: str = ""
    symbol: str = ""
    trades: list[BacktestTrade] = Field(default_factory=list)
    equity_curve: list[float] = Field(default_factory=list)
    start_balance: float = 10_000.0
    stopped_early: bool = False
    error: str | None = None

    @property
    def closed_trades(self) -> list[BacktestTrade]:
        return [
            t for t in self.trades
            if t.outcome in (TradeOutcome.WIN, TradeOutcome.LOSS, TradeOutcome.BREAKEVEN)
        ]

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.outcome == TradeOutcome.WIN) / len(closed)

    @property
    def sharpe(self) -> float | None:
        pnls = [t.pnl_usd for t in self.closed_trades if t.pnl_usd is not None]
        if len(pnls) < 10:
            return None
        arr = np.array(pnls, dtype=np.float64)
        std = float(np.std(arr, ddof=1))
        if std == 0:
            return None
        return round(float(np.mean(arr) / std * (252 ** 0.5)), 3)

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        eq = np.array(self.equity_curve, dtype=np.float64)
        peak = np.maximum.accumulate(eq)
        dd = np.where(peak > 0, (eq - peak) / peak, 0.0)
        return round(float(dd.min()) * 100, 2)

    @property
    def total_pnl(self) -> float:
        return round(
            sum(t.pnl_usd for t in self.closed_trades if t.pnl_usd is not None), 2
        )

    def summary(self) -> dict[str, Any]:
        closed = self.closed_trades
        return {
            "total_trades": len(closed),
            "win_rate": round(self.win_rate, 3),
            "sharpe": self.sharpe,
            "max_dd_pct": self.max_drawdown_pct,
            "total_pnl": self.total_pnl,
            "expectancy_r": round(
                sum(t.pnl_r for t in closed if t.pnl_r is not None)
                / max(len(closed), 1),
                3,
            ),
        }


class BacktestEngine:
    """
    Async backtest engine — runs rule-based backtests on OHLC data.

    .. deprecated::
        BacktestEngine uses an arbitrary ``signal_fn`` that diverges from the live
        v4 pipeline path (TradeConstructor + compute_direction).  New code should
        use ``run_vectorbt_backtest`` from ``backtester.vbt_engine`` which shares
        the same SL/TP logic as the live trading loop.

        BacktestEngine is retained for ParallelBacktester / AutoImprover
        compatibility only.  It must NOT be used in the live trading path.

    Injected dependencies:
    - session_factory: for persisting backtest runs to DB
    - event_bus: for publishing state changes
    """

    #: Marker checked by live-path guards.  Instantiation in live mode raises.
    _LEGACY_BACKTEST_ONLY: bool = True

    def __init__(
        self,
        session_factory: async_sessionmaker | None = None,
        event_bus: EventBus | None = None,
        *,
        _allow_in_live: bool = False,
    ) -> None:
        if not _allow_in_live:
            import warnings
            warnings.warn(
                "BacktestEngine uses a signal_fn that diverges from the live v4 "
                "pipeline. Use run_vectorbt_backtest (vbt_engine) instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._session_factory = session_factory
        self._event_bus = event_bus

    async def run(
        self,
        symbol: str,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        timestamps: list[datetime] | None = None,
        balance: float = 10_000.0,
        risk_pct: float = 0.01,
        filters: list[str] | None = None,
        start_idx: int = 0,
        end_idx: int | None = None,
        run_id: str | None = None,
        signal_fn: Any = None,
        stop_check: Any = None,
        # Slippage & spread simulation parameters
        spread_pips: float = 0.0,
        slippage_pips: float = 0.0,
        commission_per_lot: float = 0.0,
    ) -> BacktestResult:
        """
        Run a backtest on the given OHLC data.

        Args:
            symbol: Trading symbol.
            opens, highs, lows, closes: Price arrays.
            timestamps: Optional datetime for each bar.
            balance: Starting account balance.
            risk_pct: Risk per trade as fraction of balance.
            filters: Active filter names.
            start_idx, end_idx: Slice of data to use.
            run_id: Optional identifier for this run.
            signal_fn: Callable(bar_idx, opens, highs, lows, closes, filters)
                       -> (direction, entry, sl, tp1, tp2, setup_type, confidence) or None.
            stop_check: Callable returning True to abort.

        Returns:
            BacktestResult with trades and equity curve.
        """
        if end_idx is None:
            end_idx = len(closes) - 1

        result = BacktestResult(
            run_id=run_id or "",
            symbol=symbol,
            start_balance=balance,
        )

        if signal_fn is None:
            result.error = "No signal function provided"
            return result

        equity = balance
        equity_curve = [balance]
        trades: list[BacktestTrade] = []
        open_trade: BacktestTrade | None = None

        # Persist run start if DB available
        if run_id and self._session_factory:
            await self._update_db_state(run_id, BacktestState.RUNNING)

        i = start_idx - 1
        try:
            for i in range(start_idx, end_idx + 1):
                if stop_check and stop_check():
                    result.stopped_early = True
                    break

                # Yield to event loop every 1000 bars to prevent blocking
                if (i - start_idx) % 1000 == 0 and i > start_idx:
                    await asyncio.sleep(0)

                # Check open trade against current bar
                if open_trade is not None:
                    open_trade, closed_pnl = self._check_exit(
                        open_trade, highs[i], lows[i], closes[i],
                        timestamp=timestamps[i] if timestamps else None,
                    )
                    if closed_pnl is not None:
                        equity += closed_pnl
                        equity_curve.append(equity)
                        trades.append(open_trade)
                        open_trade = None
                        continue

                # Try to open a new trade if no position
                if open_trade is None:
                    sig = await signal_fn(
                        i, opens, highs, lows, closes, filters or [],
                        timestamps=timestamps,
                    )
                    if sig is not None:
                        direction, entry, sl, tp1, tp2, setup_type, conf = sig

                        # Apply spread + slippage simulation
                        total_slip = spread_pips + slippage_pips
                        if direction == TradeDirection.BUY:
                            entry += total_slip  # Worse fill for buys
                        else:
                            entry -= total_slip  # Worse fill for sells

                        risk_amount = equity * risk_pct
                        sl_dist = abs(entry - sl)
                        if sl_dist > 0:
                            lots = round(risk_amount / sl_dist, 4)
                        else:
                            lots = 0.01

                        # Deduct commission
                        if commission_per_lot > 0:
                            equity -= lots * commission_per_lot

                        open_trade = BacktestTrade(
                            bar_index=i,
                            direction=direction,
                            entry=entry,
                            sl=sl,
                            tp1=tp1,
                            tp2=tp2,
                            lots=lots,
                            open_time=timestamps[i] if timestamps else None,
                            setup_type=setup_type,
                            confidence=conf,
                            filters_used=filters or [],
                        )

        except Exception as exc:
            result.error = str(exc)
            logger.error("Backtest failed at bar %d: %s", i, exc)

        # Close any remaining open trade at last bar's close
        if open_trade is not None:
            open_trade.close_price = float(closes[end_idx])
            open_trade.close_time = timestamps[end_idx] if timestamps else None
            open_trade.outcome = TradeOutcome.BREAKEVEN
            sl_dist = abs(open_trade.entry - open_trade.sl)
            if sl_dist > 0:
                raw_pnl = (open_trade.close_price - open_trade.entry) * open_trade.lots
                if open_trade.direction == TradeDirection.SELL:
                    raw_pnl = -raw_pnl
                open_trade.pnl_usd = round(raw_pnl, 2)
                open_trade.pnl_r = round(raw_pnl / (sl_dist * open_trade.lots), 2)
            else:
                open_trade.pnl_usd = 0.0
                open_trade.pnl_r = 0.0
            equity += open_trade.pnl_usd
            equity_curve.append(equity)
            trades.append(open_trade)

        result.trades = trades
        result.equity_curve = equity_curve

        # Persist completion
        if run_id and self._session_factory:
            state = BacktestState.COMPLETED if not result.error else BacktestState.FAILED
            await self._update_db_state(
                run_id, state,
                best_sharpe=result.sharpe,
                best_wr=result.win_rate,
                best_pnl=result.total_pnl,
                best_dd=result.max_drawdown_pct,
                best_trades=len(result.closed_trades),
            )

        return result

    async def run_holdout_validation(
        self,
        symbol: str,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        timestamps: list[datetime] | None = None,
        balance: float = 10_000.0,
        risk_pct: float = 0.01,
        filters: list[str] | None = None,
        signal_fn: Any = None,
        holdout_bars: int = 500,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Run a walk-forward holdout validation on the final N bars of the dataset.

        The holdout slice is strictly the last ``holdout_bars`` bars — it must
        **not** overlap with the in-sample or OOS window used for strategy
        optimisation.  Returns a summary dict compatible with
        ``DeploymentPipeline.evaluate_promotion(holdout_result=...)``.

        Args:
            symbol: Trading symbol.
            opens, highs, lows, closes: Full price arrays (all available data).
            timestamps: Optional datetime per bar.
            balance: Starting balance for the simulation.
            risk_pct: Risk per trade as fraction of balance.
            filters: Active filter names.
            signal_fn: Signal function (same signature as ``run()``).
            holdout_bars: Number of trailing bars to reserve as holdout slice.
            run_id: Optional identifier.

        Returns:
            dict with keys: sharpe, win_rate, total_trades, total_pnl, max_dd_pct,
            holdout_bars — or None if the dataset is too short.
        """
        total_bars = len(closes)
        if total_bars <= holdout_bars:
            logger.warning(
                "[holdout] Dataset too short (%d bars) for holdout_bars=%d",
                total_bars, holdout_bars,
            )
            return None

        holdout_start = total_bars - holdout_bars
        holdout_end = total_bars - 1

        logger.info(
            "[holdout] Running holdout validation on bars %d–%d (%d bars)",
            holdout_start, holdout_end, holdout_bars,
        )

        result = await self.run(
            symbol=symbol,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            timestamps=timestamps,
            balance=balance,
            risk_pct=risk_pct,
            filters=filters,
            start_idx=holdout_start,
            end_idx=holdout_end,
            run_id=run_id or f"holdout_{symbol}",
            signal_fn=signal_fn,
        )

        if result.error:
            logger.warning("[holdout] Backtest error: %s", result.error)
            return None

        summary = result.summary()
        summary["holdout_bars"] = holdout_bars
        logger.info(
            "[holdout] Result: trades=%d sharpe=%s win_rate=%.1f%% dd=%.1f%%",
            summary.get("total_trades", 0),
            summary.get("sharpe"),
            (summary.get("win_rate", 0) or 0) * 100,
            summary.get("max_dd_pct", 0) or 0,
        )
        return summary

    async def walk_forward(
        self,
        symbol: str,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        timestamps: list[datetime] | None = None,
        balance: float = 10_000.0,
        risk_pct: float = 0.01,
        filters: list[str] | None = None,
        signal_fn: Any = None,
        in_sample_bars: int = 5000,
        oos_bars: int = 1000,
        run_id: str | None = None,
    ) -> list[BacktestResult]:
        """
        Walk-forward backtest: slide IS/OOS windows across data.

        Returns a list of BacktestResult, one per OOS window.
        """
        total_bars = len(closes)
        results: list[BacktestResult] = []
        step = 0

        offset = 0
        while offset + in_sample_bars + oos_bars <= total_bars:
            oos_start = offset + in_sample_bars
            oos_end = oos_start + oos_bars - 1

            segment_id = f"{run_id}_wf{step}" if run_id else f"wf_{step}"
            bt = await self.run(
                symbol=symbol,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                timestamps=timestamps,
                balance=balance,
                risk_pct=risk_pct,
                filters=filters,
                start_idx=oos_start,
                end_idx=oos_end,
                run_id=segment_id,
                signal_fn=signal_fn,
            )
            results.append(bt)

            offset += oos_bars
            step += 1

        logger.info("Walk-forward complete: %d OOS segments", len(results))
        return results

    def _check_exit(
        self,
        trade: BacktestTrade,
        high: float,
        low: float,
        close: float,
        timestamp: datetime | None = None,
    ) -> tuple[BacktestTrade, float | None]:
        """
        Check if an open trade hits SL, TP1, or TP2 on the current bar.

        Returns (updated_trade, pnl_if_closed_or_None).
        """
        is_buy = trade.direction == TradeDirection.BUY
        sl_dist = abs(trade.entry - trade.sl)
        risk_per_r = sl_dist * trade.lots if sl_dist > 0 else 1.0

        # Check stop loss
        if (is_buy and low <= trade.sl) or (not is_buy and high >= trade.sl):
            pnl = (trade.sl - trade.entry) * trade.lots
            if not is_buy:
                pnl = -pnl
            trade = trade.model_copy(update={
                "outcome": TradeOutcome.LOSS,
                "close_price": trade.sl,
                "close_time": timestamp,
                "pnl_usd": round(pnl, 2),
                "pnl_r": round(pnl / risk_per_r, 2) if risk_per_r else -1.0,
            })
            return trade, trade.pnl_usd

        # Check TP1 (partial — simplified as full close at TP1 price)
        if (is_buy and high >= trade.tp1) or (not is_buy and low <= trade.tp1):
            pnl = (trade.tp1 - trade.entry) * trade.lots
            if not is_buy:
                pnl = -pnl
            trade = trade.model_copy(update={
                "outcome": TradeOutcome.WIN,
                "close_price": trade.tp1,
                "close_time": timestamp,
                "pnl_usd": round(pnl, 2),
                "pnl_r": round(pnl / risk_per_r, 2) if risk_per_r else 0.5,
            })
            return trade, trade.pnl_usd

        # Check TP2 (full target)
        if (is_buy and high >= trade.tp2) or (not is_buy and low <= trade.tp2):
            pnl = (trade.tp2 - trade.entry) * trade.lots
            if not is_buy:
                pnl = -pnl
            trade = trade.model_copy(update={
                "outcome": TradeOutcome.WIN,
                "close_price": trade.tp2,
                "close_time": timestamp,
                "pnl_usd": round(pnl, 2),
                "pnl_r": round(pnl / risk_per_r, 2) if risk_per_r else 1.0,
            })
            return trade, trade.pnl_usd

        # Still open
        return trade, None

    async def _update_db_state(self, run_id: str, state: BacktestState, **kwargs: Any) -> None:
        """Update backtest run state in DB."""
        if not self._session_factory:
            return
        try:
            async with self._session_factory() as session:
                repo = BacktestRepository(session)
                await repo.update_state(run_id, state, **kwargs)
                await session.commit()
        except Exception as exc:
            logger.error("Failed to update backtest state: %s", exc)
