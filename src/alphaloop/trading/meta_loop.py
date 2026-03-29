"""
Meta-loop — background strategy evolution loop.

Triggered by TradeClosed events. After every check_interval closed trades:
1. Check if strategy is degrading (ResearchAnalyzer)
2. If degraded: run AutoImprover to find better params
3. Create new strategy version if improved
4. Optionally auto-activate and monitor via RollbackTracker
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from alphaloop.config.settings_service import SettingsService
from alphaloop.core.events import EventBus

logger = logging.getLogger(__name__)


@dataclass
class RollbackTracker:
    """
    Monitors a new strategy version's performance.
    Uses R-multiples (pnl / risk) for size-independent Sharpe comparison.
    """

    previous_version: int
    previous_sharpe: float
    rollback_window: int = 30
    _r_multiples: list[float] = field(default_factory=list)

    def record(self, pnl_usd: float, risk_usd: float) -> None:
        """Record a trade's R-multiple."""
        r = pnl_usd / risk_usd if risk_usd > 0 else 0.0
        self._r_multiples.append(r)

    def should_rollback(self) -> bool:
        """Check if the new version underperforms the previous."""
        if len(self._r_multiples) < self.rollback_window:
            return False

        import numpy as np
        arr = np.array(self._r_multiples[-self.rollback_window:])
        if arr.std() == 0:
            return False
        current_sharpe = float(arr.mean() / arr.std())
        return current_sharpe < self.previous_sharpe * 0.7

    @property
    def is_complete(self) -> bool:
        return len(self._r_multiples) >= self.rollback_window


class MetaLoop:
    """
    Background strategy evolution loop.

    Non-blocking: all optimization work runs in asyncio.Tasks and thread pools.
    """

    def __init__(
        self,
        *,
        symbol: str,
        session_factory,
        event_bus: EventBus,
        settings_service: SettingsService,
        ai_callback=None,
        check_interval: int = 20,
        rollback_window: int = 30,
        auto_activate: bool = False,
        degradation_threshold: float = 0.7,
    ):
        self._symbol = symbol
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._settings_service = settings_service
        self._ai_callback = ai_callback
        self._check_interval = check_interval
        self._rollback_window = rollback_window
        self._auto_activate = auto_activate
        self._degradation_threshold = degradation_threshold

        self._trade_count_since_check = 0
        self._running_task: asyncio.Task | None = None
        self._rollback_tracker: RollbackTracker | None = None
        self._last_cycle_time: float = 0
        self._cooldown_seconds: float = 86400  # 24h minimum between autolearn cycles

    async def on_trade_closed(self, event) -> None:
        """Event handler — subscribed to TradeClosed events."""
        if hasattr(event, "symbol") and event.symbol != self._symbol:
            return

        self._trade_count_since_check += 1

        # Feed rollback tracker if monitoring a new version
        if self._rollback_tracker:
            pnl = getattr(event, "pnl_usd", 0)
            risk = getattr(event, "risk_usd", abs(pnl) * 2 or 1)
            self._rollback_tracker.record(pnl, risk)
            if self._rollback_tracker.should_rollback():
                await self._execute_rollback()
                return

        # Check if it's time to run a research cycle
        if self._trade_count_since_check >= self._check_interval:
            if self._running_task is None or self._running_task.done():
                if time.time() - self._last_cycle_time > self._cooldown_seconds:
                    self._running_task = asyncio.create_task(
                        self._run_cycle()
                    )
                    self._trade_count_since_check = 0

    async def _run_cycle(self) -> None:
        """Single meta-loop cycle. Runs as background task."""
        try:
            self._last_cycle_time = time.time()

            from alphaloop.research.analyzer import ResearchAnalyzer

            analyzer = ResearchAnalyzer(
                session_factory=self._session_factory,
                event_bus=self._event_bus,
                ai_callback=self._ai_callback,
            )

            # Step 1: Check if retraining needed
            retrain_check = await analyzer.check_retraining_needed(
                self._symbol,
                degradation_threshold=self._degradation_threshold,
            )

            if not retrain_check.get("needs_retraining"):
                logger.info(
                    "[meta-loop] %s: performance stable, no action needed",
                    self._symbol,
                )
                return

            logger.info(
                "[meta-loop] %s: degradation detected — starting autolearn",
                self._symbol,
            )

            # Step 2: Load current active strategy
            from alphaloop.trading.strategy_loader import load_active_strategy
            active = await load_active_strategy(self._settings_service, self._symbol)
            if active is None:
                logger.info("[meta-loop] No active strategy for %s", self._symbol)
                return

            # Step 3: Run full research report
            report = await analyzer.run(
                symbol=self._symbol,
                strategy_version=f"v{active.version}",
                lookback_days=30,
            )

            # Step 4-5: Auto-optimize would happen here
            # (AutoImprover.optimize runs in thread pool via asyncio.to_thread)
            # For now, log the research result
            if report:
                logger.info(
                    "[meta-loop] Research report for %s: trades=%d, sharpe=%s",
                    self._symbol,
                    report.get("metrics", {}).get("total_trades", 0),
                    report.get("metrics", {}).get("sharpe_ratio"),
                )

            # Step 6: If improved, create_strategy_version with source="autolearn"
            # Step 7: If auto_activate, store as active + init RollbackTracker
            # (Full implementation requires AutoImprover integration)

            # Publish completion event
            from alphaloop.core.events import MetaLoopCompleted
            await self._event_bus.publish(MetaLoopCompleted(
                symbol=self._symbol,
                action_taken="research_completed",
                details=f"Degradation ratio: {retrain_check.get('degradation_status', {})}",
            ))

        except Exception as e:
            logger.error(
                "[meta-loop] Cycle failed for %s: %s",
                self._symbol, e, exc_info=True,
            )

    async def _execute_rollback(self) -> None:
        """Rollback to previous strategy version."""
        if not self._rollback_tracker:
            return

        prev_ver = self._rollback_tracker.previous_version
        logger.warning(
            "[meta-loop] Rolling back %s to v%d (underperformance detected)",
            self._symbol, prev_ver,
        )

        # Re-activate the previous version
        from alphaloop.trading.strategy_loader import load_active_strategy
        # The previous version JSON still exists on disk — just re-activate it
        import json
        from pathlib import Path
        versions_dir = Path(__file__).resolve().parent.parent.parent.parent / "strategy_versions"
        prev_path = None
        for f in versions_dir.glob(f"{self._symbol}_v*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("version") == prev_ver:
                    prev_path = f
                    break
            except (json.JSONDecodeError, OSError):
                continue

        if prev_path:
            data = json.loads(prev_path.read_text())
            await self._settings_service.set(
                f"active_strategy_{self._symbol}",
                json.dumps({
                    "symbol": self._symbol,
                    "version": prev_ver,
                    "status": data.get("status", ""),
                    "params": data.get("params", {}),
                    "tools": data.get("tools", {}),
                    "validation": data.get("validation", {}),
                    "ai_models": data.get("ai_models", {}),
                    "signal_mode": data.get("signal_mode", "algo_plus_ai"),
                }),
            )

        from alphaloop.core.events import StrategyRolledBack
        await self._event_bus.publish(StrategyRolledBack(
            symbol=self._symbol,
            from_version=0,  # current version unknown here
            to_version=prev_ver,
            reason="R-multiple Sharpe below 70% of previous version",
        ))

        self._rollback_tracker = None
