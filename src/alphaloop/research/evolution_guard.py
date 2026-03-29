"""
research/evolution_guard.py — Drift detection, OOS validation, rollback.

Provides safety checks before and after parameter changes:
- Drift detection: blocks changes that deviate too far from baseline
- OOS validation: verifies changes improve out-of-sample performance
- Rollback: restores previous parameters when degradation is detected
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.core.config import EvolutionConfig
from alphaloop.core.events import EventBus
from alphaloop.core.types import EvolutionEventType
from alphaloop.db.repositories.research_repo import ResearchRepository

logger = logging.getLogger(__name__)


class EvolutionGuard:
    """
    Safety guard for strategy evolution — prevents runaway parameter drift,
    validates OOS performance, and supports rollback.

    Injected dependencies:
    - session_factory: for querying snapshots and logging events
    - event_bus: for publishing evolution events
    - evolution_config: thresholds and limits
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        event_bus: EventBus,
        evolution_config: EvolutionConfig,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._evo = evolution_config

    async def check_drift(
        self,
        current_params: dict[str, Any],
        baseline_params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Check if current parameters have drifted too far from baseline.

        Returns:
            dict with "passed", "total_drift", "drifted_params" keys.
        """
        drifted: list[dict[str, Any]] = []
        max_drift = 0.0

        for key, baseline_val in baseline_params.items():
            if not isinstance(baseline_val, (int, float)) or baseline_val == 0:
                continue
            current_val = current_params.get(key)
            if current_val is None or not isinstance(current_val, (int, float)):
                continue

            drift = abs(current_val - baseline_val) / abs(baseline_val)
            if drift > self._evo.drift_block_threshold:
                drifted.append({
                    "parameter": key,
                    "baseline": baseline_val,
                    "current": current_val,
                    "drift_pct": round(drift * 100, 1),
                })
            max_drift = max(max_drift, drift)

        passed = len(drifted) == 0
        if not passed:
            logger.warning(
                "Drift check FAILED: %d params exceeded threshold (max drift: %.1f%%)",
                len(drifted), max_drift * 100,
            )

        return {
            "passed": passed,
            "max_drift_pct": round(max_drift * 100, 1),
            "drifted_params": drifted,
        }

    async def validate_oos(
        self,
        oos_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Validate that out-of-sample performance meets minimum thresholds.

        Returns:
            dict with "passed", "reasons" keys.
        """
        reasons: list[str] = []

        win_rate = oos_metrics.get("win_rate", 0.0)
        if win_rate < self._evo.oos_min_wr:
            reasons.append(
                f"OOS win_rate {win_rate:.1%} below minimum {self._evo.oos_min_wr:.1%}"
            )

        sharpe = oos_metrics.get("sharpe_ratio")
        if sharpe is not None and sharpe < 0:
            reasons.append(f"OOS Sharpe {sharpe:.2f} is negative")

        trade_count = oos_metrics.get("total_trades", 0)
        if trade_count < 5:
            reasons.append(f"OOS trade count {trade_count} too low for validation")

        passed = len(reasons) == 0
        if not passed:
            logger.warning("OOS validation FAILED: %s", "; ".join(reasons))

        return {"passed": passed, "reasons": reasons}

    async def rollback(
        self,
        symbol: str | None = None,
        strategy_version: str | None = None,
        reason: str = "degradation_detected",
    ) -> dict[str, Any] | None:
        """
        Retrieve the latest parameter snapshot for rollback.

        Returns the snapshot parameters dict, or None if no snapshot exists.
        Logs an evolution event for audit.
        """
        async with self._session_factory() as session:
            repo = ResearchRepository(session)

            snapshot = await repo.get_latest_snapshot()
            if snapshot is None:
                logger.warning("No snapshot available for rollback")
                return None

            params = snapshot.parameters
            logger.info(
                "Rolling back to snapshot from %s (trigger: %s)",
                snapshot.snapped_at, snapshot.trigger,
            )

            # Log the rollback event
            await repo.create_evolution_event(
                symbol=symbol,
                strategy_version=strategy_version,
                event_type=EvolutionEventType.ROLLBACK,
                params_after=params,
                details=f"Rollback reason: {reason}",
            )
            await session.commit()

        return params

    async def log_event(
        self,
        event_type: EvolutionEventType,
        symbol: str | None = None,
        strategy_version: str | None = None,
        metrics_before: dict[str, Any] | None = None,
        metrics_after: dict[str, Any] | None = None,
        params_before: dict[str, Any] | None = None,
        params_after: dict[str, Any] | None = None,
        details: str | None = None,
    ) -> None:
        """Persist an evolution event for audit trail."""
        try:
            async with self._session_factory() as session:
                repo = ResearchRepository(session)
                await repo.create_evolution_event(
                    symbol=symbol,
                    strategy_version=strategy_version,
                    event_type=event_type,
                    metrics_before=metrics_before,
                    metrics_after=metrics_after,
                    params_before=params_before,
                    params_after=params_after,
                    details=details,
                )
                await session.commit()
        except Exception as exc:
            logger.error("Failed to log evolution event: %s", exc)
