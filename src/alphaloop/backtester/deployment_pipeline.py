"""
backtester/deployment_pipeline.py — Stage transitions for strategies.

Manages the lifecycle: sandbox -> staging -> live, with validation
gates at each transition.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from alphaloop.core.config import EvolutionConfig
from alphaloop.core.events import EventBus
from alphaloop.core.types import EvolutionEventType, StrategyStatus
from alphaloop.db.repositories.research_repo import ResearchRepository

logger = logging.getLogger(__name__)


class StageGate(object):
    """Defines the validation requirements for a stage transition."""

    def __init__(
        self,
        from_status: StrategyStatus,
        to_status: StrategyStatus,
        min_trades: int = 0,
        min_sharpe: float | None = None,
        min_win_rate: float | None = None,
        max_drawdown_pct: float | None = None,
        min_cycles: int = 0,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.min_trades = min_trades
        self.min_sharpe = min_sharpe
        self.min_win_rate = min_win_rate
        self.max_drawdown_pct = max_drawdown_pct
        self.min_cycles = min_cycles


# Default stage gates
DEFAULT_GATES: list[StageGate] = [
    StageGate(
        from_status=StrategyStatus.CANDIDATE,
        to_status=StrategyStatus.DRY_RUN,
        min_trades=40,
        min_sharpe=1.0,
        min_win_rate=0.42,
        max_drawdown_pct=-25.0,
    ),
    StageGate(
        from_status=StrategyStatus.DRY_RUN,
        to_status=StrategyStatus.DEMO,
        min_trades=50,
        min_sharpe=0.5,
        min_win_rate=0.42,
        max_drawdown_pct=-20.0,
        min_cycles=3,
    ),
    StageGate(
        from_status=StrategyStatus.DEMO,
        to_status=StrategyStatus.LIVE,
        min_trades=100,
        min_sharpe=0.7,
        min_win_rate=0.45,
        max_drawdown_pct=-15.0,
        min_cycles=5,
    ),
]


class DeploymentPipeline:
    """
    Manages strategy promotion through deployment stages.

    sandbox (candidate) -> dry_run -> demo -> live

    Each transition requires passing a StageGate validation.

    Injected dependencies:
    - session_factory: for logging evolution events
    - event_bus: for publishing promotion events
    - evolution_config: for thresholds
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        event_bus: EventBus,
        evolution_config: EvolutionConfig,
        gates: list[StageGate] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._evo = evolution_config
        self._gates = gates or DEFAULT_GATES

    async def evaluate_promotion(
        self,
        current_status: StrategyStatus,
        metrics: dict[str, Any],
        cycles_completed: int = 0,
        pnl_history: list[float] | None = None,
        holdout_result: dict[str, Any] | None = None,
        bypass_candidate_gate: bool = False,
    ) -> dict[str, Any]:
        """
        Evaluate whether a strategy is ready for promotion.

        Includes Monte Carlo robustness check for demo→live transitions.

        Args:
            current_status: Current deployment stage.
            metrics: Current performance metrics.
            cycles_completed: Number of evaluation cycles completed.
            pnl_history: Optional trade PnL list for Monte Carlo analysis.
            holdout_result: Walk-forward holdout validation result dict.
                Required for DEMO → LIVE. Must contain 'sharpe' key.
                Pass None to block promotion with a clear reason.

        Returns:
            dict with "eligible", "target_status", "reasons", "monte_carlo".
        """
        gate = self._find_gate(current_status)
        if gate is None:
            return {
                "eligible": False,
                "target_status": None,
                "reasons": [f"No promotion path from {current_status}"],
            }

        # Walk-forward rejection blocks ALL promotion paths regardless of metrics.
        # The operator must re-train or manually override status before promoting.
        if current_status == StrategyStatus.WF_REJECTED:
            return {
                "eligible": False,
                "target_status": None,
                "reasons": [
                    "Strategy failed walk-forward gate (status=wf_rejected). "
                    "Re-train with more data or set status=candidate manually to override."
                ],
            }

        if (
            current_status == StrategyStatus.CANDIDATE
            and bypass_candidate_gate
        ):
            return {
                "eligible": True,
                "target_status": gate.to_status,
                "reasons": [],
            }

        reasons: list[str] = []

        trade_count = metrics.get("total_trades", 0)
        if trade_count < gate.min_trades:
            reasons.append(
                f"Trades {trade_count} < required {gate.min_trades}"
            )

        if gate.min_sharpe is not None:
            sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe")
            if sharpe is None or sharpe < gate.min_sharpe:
                reasons.append(
                    f"Sharpe {sharpe} < required {gate.min_sharpe}"
                )

        if gate.min_win_rate is not None:
            wr = metrics.get("win_rate", 0)
            if wr < gate.min_win_rate:
                reasons.append(
                    f"Win rate {wr:.1%} < required {gate.min_win_rate:.1%}"
                )

        if gate.max_drawdown_pct is not None:
            dd = metrics.get("max_drawdown_pct", 0)
            if dd < gate.max_drawdown_pct:  # dd is negative
                reasons.append(
                    f"Drawdown {dd:.1f}% exceeds limit {gate.max_drawdown_pct:.1f}%"
                )

        if cycles_completed < gate.min_cycles:
            reasons.append(
                f"Cycles {cycles_completed} < required {gate.min_cycles}"
            )

        # Walk-forward holdout check (required for DEMO → LIVE)
        if gate.to_status == StrategyStatus.LIVE:
            if holdout_result is None:
                reasons.append(
                    "Walk-forward holdout not provided — required for DEMO → LIVE promotion. "
                    "Run backtester with holdout set and pass result."
                )
            else:
                holdout_sharpe = holdout_result.get("sharpe") or holdout_result.get("sharpe_ratio")
                holdout_min = 0.3  # minimum Sharpe on holdout slice
                if holdout_sharpe is None:
                    reasons.append("Holdout result missing 'sharpe' field")
                elif holdout_sharpe < holdout_min:
                    reasons.append(
                        f"Holdout Sharpe {holdout_sharpe:.2f} < required {holdout_min} "
                        f"— strategy may be overfit to in-sample data"
                    )

        # Monte Carlo robustness check for promotions to demo or live
        mc_result = None
        if (
            pnl_history
            and len(pnl_history) >= 20
            and gate.to_status in (StrategyStatus.DEMO, StrategyStatus.LIVE)
        ):
            try:
                from alphaloop.research.monte_carlo import MonteCarloSimulator
                mc = MonteCarloSimulator(n_simulations=2000)
                significance = await mc.run_significance_test(pnl_history)
                ruin = await mc.run_ruin_probability(pnl_history)
                mc_result = {
                    "significance": significance,
                    "ruin": ruin,
                }
                if significance.get("status") == "complete":
                    if not significance.get("is_significant", False):
                        reasons.append(
                            f"Monte Carlo: Sharpe not statistically significant "
                            f"(p={significance.get('p_value', '?')})"
                        )
                if ruin.get("status") == "complete":
                    ruin_prob = ruin.get("ruin_probability", 0)
                    if ruin_prob > 0.10:
                        reasons.append(
                            f"Monte Carlo: Ruin probability {ruin_prob:.1%} > 10% threshold"
                        )
            except Exception as exc:
                logger.warning("Monte Carlo check failed: %s", exc)

        eligible = len(reasons) == 0
        result = {
            "eligible": eligible,
            "target_status": gate.to_status if eligible else None,
            "reasons": reasons,
        }
        if mc_result:
            result["monte_carlo"] = mc_result
        return result

    async def promote(
        self,
        symbol: str,
        strategy_version: str,
        current_status: StrategyStatus,
        metrics: dict[str, Any],
        cycles_completed: int = 0,
        bypass_candidate_gate: bool = False,
    ) -> dict[str, Any]:
        """
        Attempt to promote a strategy to the next stage.

        Returns dict with "promoted", "new_status", "reasons".
        """
        evaluation = await self.evaluate_promotion(
            current_status, metrics, cycles_completed,
            bypass_candidate_gate=bypass_candidate_gate,
        )

        if not evaluation["eligible"]:
            logger.info(
                "Strategy %s/%s not eligible for promotion: %s",
                symbol, strategy_version, evaluation["reasons"],
            )
            return {
                "promoted": False,
                "new_status": current_status,
                "reasons": evaluation["reasons"],
            }

        new_status = evaluation["target_status"]

        # Log the promotion event
        async with self._session_factory() as session:
            repo = ResearchRepository(session)
            await repo.create_evolution_event(
                symbol=symbol,
                strategy_version=strategy_version,
                event_type=EvolutionEventType.PROMOTE,
                metrics_after=metrics,
                details=f"Promoted from {current_status} to {new_status}",
            )
            await session.commit()

        logger.info(
            "Strategy %s/%s promoted: %s -> %s",
            symbol, strategy_version, current_status, new_status,
        )

        return {
            "promoted": True,
            "new_status": new_status,
            "reasons": [],
        }

    def _find_gate(self, from_status: StrategyStatus) -> StageGate | None:
        """Find the gate for the given source status."""
        for gate in self._gates:
            if gate.from_status == from_status:
                return gate
        return None

    async def start_canary(
        self,
        symbol: str,
        strategy_version: str,
        allocation_pct: float = 10.0,
        duration_hours: int = 24,
    ) -> dict[str, Any]:
        """
        Start a canary deployment for a strategy.

        The canary runs the new strategy alongside the current live strategy
        with a reduced allocation (e.g. 10% of normal position size).

        Args:
            symbol: Trading symbol.
            strategy_version: Strategy version to canary test.
            allocation_pct: Percentage of normal position size (default 10%).
            duration_hours: How long to run the canary (default 24h).

        Returns:
            dict with "canary_id", "status", "start_time", "end_time".
        """
        import time
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        end_time = now + timedelta(hours=duration_hours)

        canary_id = f"canary_{symbol}_{int(time.time())}"

        # Log the canary start event
        async with self._session_factory() as session:
            repo = ResearchRepository(session)
            await repo.create_evolution_event(
                symbol=symbol,
                strategy_version=strategy_version,
                event_type=EvolutionEventType.CANARY_START,
                details=(
                    f"Canary deployment started: {allocation_pct}% allocation "
                    f"for {duration_hours}h"
                ),
                params_after={
                    "canary_id": canary_id,
                    "allocation_pct": allocation_pct,
                    "duration_hours": duration_hours,
                    "end_time": end_time.isoformat(),
                },
            )
            await session.commit()

        logger.info(
            "Canary %s started: %s/%s at %.0f%% for %dh",
            canary_id, symbol, strategy_version, allocation_pct, duration_hours,
        )

        return {
            "canary_id": canary_id,
            "symbol": symbol,
            "strategy_version": strategy_version,
            "allocation_pct": allocation_pct,
            "status": "running",
            "start_time": now.isoformat(),
            "end_time": end_time.isoformat(),
        }

    async def end_canary(
        self,
        symbol: str,
        strategy_version: str,
        canary_id: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """
        End a canary deployment and evaluate results.

        Compares canary performance to the baseline and recommends
        whether to promote or reject.

        Args:
            symbol: Trading symbol.
            strategy_version: Strategy version that was canary-tested.
            canary_id: Canary deployment ID.
            metrics: Performance metrics from the canary period.

        Returns:
            dict with "recommendation", "metrics_comparison", "reasons".
        """
        # Simple evaluation: if canary Sharpe > 0 and win_rate > 35%, recommend promote
        sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe", 0)
        wr = metrics.get("win_rate", 0)
        trades = metrics.get("total_trades", 0)

        reasons: list[str] = []
        if trades < 5:
            reasons.append(f"Too few trades ({trades}) for reliable evaluation")
        if sharpe is not None and sharpe < 0:
            reasons.append(f"Negative Sharpe ({sharpe:.3f}) during canary")
        if wr < 0.35:
            reasons.append(f"Low win rate ({wr:.1%}) during canary")

        recommend = len(reasons) == 0 and trades >= 5

        # Log the canary end event
        async with self._session_factory() as session:
            repo = ResearchRepository(session)
            await repo.create_evolution_event(
                symbol=symbol,
                strategy_version=strategy_version,
                event_type=EvolutionEventType.CANARY_END,
                metrics_after=metrics,
                details=(
                    f"Canary {canary_id} ended: "
                    f"{'RECOMMEND PROMOTE' if recommend else 'REJECT'} — "
                    f"{', '.join(reasons) if reasons else 'metrics OK'}"
                ),
            )
            await session.commit()

        logger.info(
            "Canary %s ended: %s — %s",
            canary_id,
            "RECOMMEND" if recommend else "REJECT",
            reasons or ["passed all checks"],
        )

        return {
            "canary_id": canary_id,
            "recommendation": "promote" if recommend else "reject",
            "metrics": metrics,
            "reasons": reasons,
        }
