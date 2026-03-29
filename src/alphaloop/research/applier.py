"""
research/applier.py — Parameter application with guardrails.

Applies research-recommended parameter changes safely, enforcing:
- Maximum per-cycle change limits
- Total drift caps from baseline
- Snapshot creation before any mutation
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.core.config import EvolutionConfig
from alphaloop.core.events import ConfigChanged, EventBus
from alphaloop.db.repositories.research_repo import ResearchRepository

logger = logging.getLogger(__name__)


class ParameterApplier:
    """
    Applies research-recommended parameter changes with safety guardrails.

    Injected dependencies:
    - session_factory: for persisting snapshots
    - event_bus: for publishing ConfigChanged events
    - evolution_config: drift and change limits
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

    async def apply_suggestions(
        self,
        current_params: dict[str, Any],
        suggestions: list[dict[str, Any]],
        baseline_params: dict[str, Any] | None = None,
        trigger: str = "research",
    ) -> dict[str, Any]:
        """
        Apply parameter suggestions with guardrails.

        Returns dict with:
        - applied: list of changes that were applied
        - rejected: list of changes that were blocked
        - new_params: the updated parameter dict
        """
        baseline = baseline_params or current_params
        applied: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        new_params = dict(current_params)

        for suggestion in suggestions:
            param = suggestion.get("parameter", "")
            suggested = suggestion.get("suggested_value")
            if not param or suggested is None:
                continue
            if param not in current_params:
                rejected.append({**suggestion, "reason": "unknown_parameter"})
                continue

            current_val = current_params[param]
            if not isinstance(current_val, (int, float)):
                rejected.append({**suggestion, "reason": "non_numeric_parameter"})
                continue

            suggested_val = float(suggested)

            # Guardrail 1: per-cycle change limit
            if not self._within_cycle_limit(current_val, suggested_val):
                rejected.append({
                    **suggestion,
                    "reason": f"exceeds_max_change_{self._evo.max_param_change_pct:.0%}",
                })
                continue

            # Guardrail 2: total drift from baseline
            baseline_val = baseline.get(param, current_val)
            if isinstance(baseline_val, (int, float)) and baseline_val != 0:
                drift = abs(suggested_val - baseline_val) / abs(baseline_val)
                if drift > self._evo.max_total_drift_pct:
                    rejected.append({
                        **suggestion,
                        "reason": f"exceeds_total_drift_{self._evo.max_total_drift_pct:.0%}",
                    })
                    continue

            new_params[param] = suggested_val
            applied.append({
                "parameter": param,
                "from": current_val,
                "to": suggested_val,
            })

        # Persist snapshot before changes take effect
        if applied:
            await self._save_snapshot(current_params, trigger)
            await self._event_bus.publish(
                ConfigChanged(
                    keys=[c["parameter"] for c in applied],
                    source=trigger,
                )
            )

        result = {
            "applied": applied,
            "rejected": rejected,
            "new_params": new_params,
        }
        logger.info(
            "Parameter applier: %d applied, %d rejected",
            len(applied), len(rejected),
        )
        return result

    def _within_cycle_limit(self, current: float, suggested: float) -> bool:
        """Check if the change is within the max per-cycle change percentage."""
        if current == 0:
            return abs(suggested) < 1e-6
        change_pct = abs(suggested - current) / abs(current)
        return change_pct <= self._evo.max_param_change_pct

    async def _save_snapshot(
        self, params: dict[str, Any], trigger: str
    ) -> None:
        """Save a parameter snapshot for rollback support."""
        try:
            async with self._session_factory() as session:
                repo = ResearchRepository(session)
                await repo.create_snapshot(
                    trigger=trigger,
                    parameters=params,
                    notes=f"Pre-change snapshot at {datetime.now(timezone.utc).isoformat()}",
                )
                await session.commit()
        except Exception as exc:
            logger.error("Failed to save parameter snapshot: %s", exc)
