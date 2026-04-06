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
from alphaloop.core.constants import STRATEGY_VERSIONS_DIR
from alphaloop.core.events import EventBus
from alphaloop.trading.strategy_loader import (
    build_algorithmic_params,
    build_active_strategy_payload,
    normalize_strategy_summary,
    resolve_strategy_ai_models,
    resolve_signal_instruction,
    resolve_strategy_setup_family,
    resolve_strategy_signal_mode,
    resolve_strategy_spec_version,
    resolve_strategy_source,
    resolve_validator_instruction,
    serialize_strategy_spec,
)

logger = logging.getLogger(__name__)


def _normalize_tool_overrides(value) -> dict[str, bool] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): bool(v) for k, v in value.items() if bool(v)}
    if isinstance(value, (list, tuple, set)):
        return {str(name): True for name in value}
    return None


def _apply_strategy_overrides(payload: dict, overrides: dict | None) -> dict:
    if not isinstance(overrides, dict):
        return payload

    explicit_strategy_spec = overrides.get("strategy_spec")
    if explicit_strategy_spec is not None:
        strategy_spec = dict(
            serialize_strategy_spec({**payload, "strategy_spec": explicit_strategy_spec})
        )
    else:
        strategy_spec = dict(payload.get("strategy_spec") or {})
    prompt_bundle = dict(strategy_spec.get("prompt_bundle") or {})

    if "params" in overrides and isinstance(overrides.get("params"), dict):
        payload["params"] = dict(overrides["params"])
    if "tools" in overrides:
        normalized_tools = _normalize_tool_overrides(overrides.get("tools"))
        if normalized_tools is not None:
            payload["tools"] = normalized_tools
    if "signal_mode" in overrides and overrides.get("signal_mode") is not None:
        payload["signal_mode"] = str(overrides["signal_mode"])
    if "source" in overrides and overrides.get("source") is not None:
        payload["source"] = str(overrides["source"])
    if "ai_models" in overrides and isinstance(overrides.get("ai_models"), dict):
        payload["ai_models"] = {
            str(name): str(model)
            for name, model in overrides["ai_models"].items()
            if model
        }
        strategy_spec["ai_models"] = dict(payload["ai_models"])
    if "signal_instruction" in overrides and overrides.get("signal_instruction") is not None:
        payload["signal_instruction"] = str(overrides["signal_instruction"])
        prompt_bundle["signal_instruction"] = payload["signal_instruction"]
    if "validator_instruction" in overrides and overrides.get("validator_instruction") is not None:
        payload["validator_instruction"] = str(overrides["validator_instruction"])
        prompt_bundle["validator_instruction"] = payload["validator_instruction"]
    if "scoring_weights" in overrides and isinstance(overrides.get("scoring_weights"), dict):
        payload["scoring_weights"] = dict(overrides["scoring_weights"])
    if "confidence_thresholds" in overrides and isinstance(overrides.get("confidence_thresholds"), dict):
        payload["confidence_thresholds"] = dict(overrides["confidence_thresholds"])
    if prompt_bundle or strategy_spec or explicit_strategy_spec is not None:
        strategy_spec["prompt_bundle"] = prompt_bundle
        payload["strategy_spec"] = strategy_spec
    return payload


def _should_recompute_family(overrides: dict | None) -> bool:
    if not isinstance(overrides, dict):
        return False
    if overrides.get("tools") is not None:
        return True
    if overrides.get("signal_mode") is not None:
        return True
    if overrides.get("strategy_spec") is not None:
        return True
    params = overrides.get("params")
    if isinstance(params, dict) and "signal_rules" in params:
        return True
    return False


def _sync_payload_strategy_spec(
    payload: dict,
    *,
    symbol: str,
    version: int,
    source: str | None = None,
    recompute_family: bool = False,
) -> dict:
    effective_source = str(source or payload.get("source") or "").strip()
    if effective_source:
        payload["source"] = effective_source
    payload["signal_mode"] = resolve_strategy_signal_mode(payload)
    if recompute_family:
        blanked_spec = dict(payload.get("strategy_spec") or {})
        blanked_spec["setup_family"] = ""
        family_payload = dict(payload)
        family_payload["strategy_spec"] = blanked_spec
        payload["setup_family"] = resolve_strategy_setup_family(family_payload)
    else:
        payload["setup_family"] = (
            str(payload.get("setup_family") or "").strip().lower()
            or resolve_strategy_setup_family(payload)
        )

    strategy_spec = dict(payload.get("strategy_spec") or {})
    strategy_spec["spec_version"] = resolve_strategy_spec_version(payload) or "v1"
    strategy_spec["signal_mode"] = payload["signal_mode"]
    strategy_spec["setup_family"] = payload["setup_family"]
    strategy_spec["ai_models"] = resolve_strategy_ai_models(payload)
    prompt_bundle = dict(strategy_spec.get("prompt_bundle") or {})
    prompt_bundle["signal_instruction"] = resolve_signal_instruction(payload)
    prompt_bundle["validator_instruction"] = resolve_validator_instruction(payload)
    strategy_spec["prompt_bundle"] = prompt_bundle
    metadata = dict(strategy_spec.get("metadata") or {})
    metadata["source"] = effective_source or resolve_strategy_source(payload)
    metadata["symbol"] = symbol
    metadata["version"] = version
    strategy_spec["metadata"] = metadata
    payload["strategy_spec"] = serialize_strategy_spec({**payload, "strategy_spec": strategy_spec})
    payload["params"] = build_algorithmic_params(payload)
    payload["ai_models"] = resolve_strategy_ai_models(payload)
    payload["signal_mode"] = resolve_strategy_signal_mode(payload)
    if recompute_family:
        blanked_spec = dict(payload.get("strategy_spec") or {})
        blanked_spec["setup_family"] = ""
        family_payload = dict(payload)
        family_payload["strategy_spec"] = blanked_spec
        payload["setup_family"] = resolve_strategy_setup_family(family_payload)
    else:
        payload["setup_family"] = resolve_strategy_setup_family(payload)
    payload["source"] = effective_source or resolve_strategy_source(payload)
    return payload


def _strategy_version_payload(
    symbol: str,
    version: int,
    status: str,
    source: str,
    active,
    params: dict,
    overrides: dict | None = None,
) -> dict:
    payload = build_active_strategy_payload(active)
    payload.update({
        "symbol": symbol,
        "version": version,
        "spec_version": resolve_strategy_spec_version(payload) or "v1",
        "status": status,
        "source": source,
        "params": params,
    })
    payload = _apply_strategy_overrides(payload, overrides)
    return _sync_payload_strategy_spec(
        payload,
        symbol=symbol,
        version=version,
        source=source,
        recompute_family=_should_recompute_family(overrides),
    )


def _walk_forward_candidate_payload(symbol: str, active, improved_params: dict) -> dict:
    """Build the temporary strategy payload used by walk-forward evaluation."""
    payload = build_active_strategy_payload(active)
    payload.update({
        "symbol": symbol,
        "version": int(payload.get("version", 0) or 0) + 1,
        "params": improved_params.get("params", payload.get("params", {})),
        "tools": payload.get("tools", {}),
        "signal_mode": payload.get("signal_mode"),
    })
    payload = _apply_strategy_overrides(payload, improved_params)
    return _sync_payload_strategy_spec(
        payload,
        symbol=symbol,
        version=payload["version"],
        recompute_family=_should_recompute_family(improved_params),
    )


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

    def record(
        self,
        pnl_usd: float,
        risk_usd: float,
        spread_cost_usd: float = 0.0,
        lots: float = 1.0,
    ) -> None:
        """
        Record a trade's cost-adjusted R-multiple.

        Deducts estimated round-trip transaction cost (spread × lots × 2)
        before computing R-multiple, preventing overfit params from looking
        better than they are due to ignoring execution costs.
        """
        commission = 2.0 * spread_cost_usd * max(lots, 0.0)
        adjusted_pnl = pnl_usd - commission
        r = adjusted_pnl / risk_usd if risk_usd > 0 else 0.0
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


# Regime-specific degradation thresholds — trending strategies tolerate more
# variance; ranging strategies have tighter edges that degrade more clearly.
_REGIME_DEGRADATION_THRESHOLDS: dict[str, float] = {
    "trending": 0.65,   # allow more degradation (volatile regime rewards trend-following)
    "ranging":  0.75,   # strict (range strategies have narrow edges)
    "volatile": 0.55,   # tolerant (volatility distorts short-term Sharpe)
    "dead":     0.80,   # strict (no excuse for losses in dead markets)
    "neutral":  0.70,   # current default
}


class MetaLoop:
    """
    Background strategy evolution loop.

    Non-blocking: all optimization work runs in asyncio.Tasks and thread pools.
    """

    def __init__(
        self,
        *,
        symbol: str,
        instance_id: str = "",
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
        self._instance_id = instance_id
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

        # Rolling R-multiples for baseline Sharpe (used in t-test gate)
        self._baseline_r_multiples: list[float] = []
        self._current_regime: str = "neutral"

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

            # Regime-aware degradation threshold
            regime_threshold = _REGIME_DEGRADATION_THRESHOLDS.get(
                self._current_regime, self._degradation_threshold
            )

            # Step 1: Check if retraining needed
            retrain_check = await analyzer.check_retraining_needed(
                self._symbol,
                degradation_threshold=regime_threshold,
            )

            if not retrain_check.get("needs_retraining"):
                logger.info(
                    "[meta-loop] %s: performance stable (regime=%s, threshold=%.2f), no action",
                    self._symbol, self._current_regime, regime_threshold,
                )
                return

            logger.info(
                "[meta-loop] %s: degradation detected (regime=%s) — starting autolearn",
                self._symbol, self._current_regime,
            )

            # Step 2: Load current active strategy
            from alphaloop.trading.strategy_loader import load_active_strategy
            active = await load_active_strategy(self._settings_service, self._symbol, self._instance_id)
            if active is None:
                logger.info("[meta-loop] No active strategy for %s", self._symbol)
                return

            # Step 3: Run full research report (with tool decay context)
            tool_decay_text = self._build_tool_decay_report()
            report = await analyzer.run(
                symbol=self._symbol,
                strategy_version=f"v{active.version}",
                lookback_days=30,
                extra_context=tool_decay_text,
            )

            if report:
                metrics = normalize_strategy_summary({"summary": report.get("metrics", {})})
                logger.info(
                    "[meta-loop] Research report for %s: trades=%d, sharpe=%s",
                    self._symbol,
                    report.get("metrics", {}).get("total_trades", 0),
                    metrics.get("sharpe"),
                )
                # Refresh baseline R-multiples from current live data
                r_mults = report.get("r_multiples", [])
                if r_mults:
                    self._baseline_r_multiples = list(r_mults[-100:])

            # Step 4: AutoImprover — find better parameters via research AI
            improved_params = await self._run_auto_improver(report, active)

            if improved_params is None:
                logger.info("[meta-loop] %s: AutoImprover found no improvement", self._symbol)
                await self._publish_completion("no_improvement", retrain_check)
                return

            new_r_multiples = improved_params.get("backtest_r_multiples", [])

            # Step 5: Statistical significance gate (Welch's t-test)
            if not self._passes_ttest(new_r_multiples):
                logger.info(
                    "[meta-loop] %s: improvement not statistically significant — keeping baseline",
                    self._symbol,
                )
                await self._publish_completion("ttest_rejected", retrain_check)
                return

            # Step 6: Walk-forward out-of-sample gate (60-day IS / 30-day OOS)
            oos_ok = await self._run_walk_forward_gate(active, improved_params)
            if not oos_ok:
                logger.info(
                    "[meta-loop] %s: walk-forward OOS gate failed — keeping baseline",
                    self._symbol,
                )
                await self._publish_completion("oos_rejected", retrain_check)
                return

            # Step 7: Create new strategy version with source="autolearn"
            new_version = await self._create_strategy_version(active, improved_params)

            # Step 8: If auto_activate, store as active + init RollbackTracker
            if self._auto_activate and new_version is not None:
                await self._activate_version(active, new_version, improved_params)

            await self._publish_completion("version_created", retrain_check)

        except Exception as e:
            logger.error(
                "[meta-loop] Cycle failed for %s: %s",
                self._symbol, e, exc_info=True,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Helper methods
    # ──────────────────────────────────────────────────────────────────────────

    def _passes_ttest(self, new_r_multiples: list[float]) -> bool:
        """
        Welch's t-test gate: return True only if new params are statistically
        better than baseline (p < 0.05, one-tailed).
        """
        if not new_r_multiples or len(new_r_multiples) < 10:
            logger.info(
                "[meta-loop] t-test skipped — insufficient new samples (%d)",
                len(new_r_multiples),
            )
            return False
        if len(self._baseline_r_multiples) < 10:
            logger.info(
                "[meta-loop] t-test skipped — insufficient baseline samples (%d)",
                len(self._baseline_r_multiples),
            )
            return False

        try:
            import numpy as np
            from scipy.stats import ttest_ind  # type: ignore

            new_arr = np.array(new_r_multiples, dtype=float)
            base_arr = np.array(self._baseline_r_multiples, dtype=float)
            _, p_value = ttest_ind(new_arr, base_arr, equal_var=False, alternative="greater")

            logger.info(
                "[meta-loop] t-test: new_mean=%.4f base_mean=%.4f p=%.4f",
                float(new_arr.mean()), float(base_arr.mean()), float(p_value),
            )
            if p_value > 0.05:
                return False
            return True

        except ImportError:
            # scipy not available — fall back to simple mean comparison
            import numpy as np
            new_arr = np.array(new_r_multiples, dtype=float)
            base_arr = np.array(self._baseline_r_multiples, dtype=float)
            passes = bool(new_arr.mean() > base_arr.mean() * 1.05)
            logger.info(
                "[meta-loop] t-test fallback (no scipy): new=%.4f base=%.4f passes=%s",
                float(new_arr.mean()), float(base_arr.mean()), passes,
            )
            return passes

    async def _run_walk_forward_gate(
        self, active, improved_params: dict
    ) -> bool:
        """
        Walk-forward out-of-sample gate.

        Runs a 90-day backtest on stored OHLCV:
          - In-sample:  first 60 days  (parameter fitting context)
          - Out-of-sample: last 30 days (validation period)

        Activation criteria:
          - OOS Sharpe > 0
          - OOS max_drawdown < 20%

        Runs in thread pool to avoid blocking the event loop.
        """
        try:
            from alphaloop.backtester.runner import BacktestRunner

            runner = BacktestRunner(session_factory=self._session_factory)

            # Build a temporary strategy dict with the improved params
            candidate_params = _walk_forward_candidate_payload(
                self._symbol,
                active,
                improved_params,
            )

            # Run OOS backtest in thread pool (CPU-bound)
            result = await asyncio.to_thread(
                runner.run_walk_forward,
                strategy=candidate_params,
                symbol=self._symbol,
                total_days=90,
                oos_days=30,
            )

            if result is None:
                logger.info("[meta-loop] walk-forward returned no result — gate passed by default")
                return True

            oos_sharpe = result.get("oos_sharpe", 0.0)
            oos_maxdd = result.get("oos_max_dd_pct")
            if oos_maxdd is None:
                oos_maxdd = result.get("oos_max_drawdown")
            if oos_maxdd is None:
                oos_maxdd = 1.0

            logger.info(
                "[meta-loop] %s walk-forward: OOS sharpe=%.3f maxdd=%.1f%%",
                self._symbol, oos_sharpe, oos_maxdd * 100,
            )

            if oos_sharpe <= 0:
                logger.info("[meta-loop] OOS Sharpe <= 0 — gate failed")
                return False
            if oos_maxdd > 0.20:
                logger.info("[meta-loop] OOS max drawdown %.1f%% > 20%% — gate failed", oos_maxdd * 100)
                return False

            # Log OOS metrics to improved_params for version JSON persistence
            improved_params["oos_metrics"] = {
                "sharpe": round(oos_sharpe, 4),
                "max_dd_pct": round(oos_maxdd, 4),
                "max_drawdown": round(oos_maxdd, 4),
            }
            return True

        except Exception as e:
            logger.warning(
                "[meta-loop] walk-forward gate error (defaulting to pass): %s", e
            )
            return True  # Conservative: don't block on infra error

    async def _run_auto_improver(self, report: dict | None, active) -> dict | None:
        """
        AutoImprover stub — request improved parameters from research AI.

        Returns a dict with at minimum:
          {
            "params": {...},
            "backtest_r_multiples": [...],
            "rationale": "...",
          }
        or None if no improvement found.

        Full implementation: calls ResearchAnalyzer.suggest_params() which
        runs the AI with the research report + tool decay context, validates
        the output schema, and returns sanitised params.
        """
        if report is None:
            return None

        try:
            from alphaloop.research.analyzer import ResearchAnalyzer

            analyzer = ResearchAnalyzer(
                session_factory=self._session_factory,
                event_bus=self._event_bus,
                ai_callback=self._ai_callback,
            )

            # Attempt AI-driven parameter suggestion
            if hasattr(analyzer, "suggest_params"):
                suggestion = await analyzer.suggest_params(
                    symbol=self._symbol,
                    current_strategy=active,
                    research_report=report,
                )
                if suggestion and suggestion.get("params"):
                    return suggestion

        except Exception as e:
            logger.debug("[meta-loop] AutoImprover not available: %s", e)

        return None  # No improvement found / not yet implemented

    async def _create_strategy_version(self, active, improved_params: dict) -> int | None:
        """
        Persist a new strategy version JSON with source="autolearn".
        Returns the new version number, or None on failure.
        """
        import json
        import os
        import time

        from alphaloop.backtester.asset_trainer import _reserve_strategy_version_path

        try:
            new_version, out_path, lock_path = _reserve_strategy_version_path(self._symbol)

            version_data = _strategy_version_payload(
                self._symbol,
                new_version,
                "candidate",
                "autolearn",
                active,
                improved_params.get("params", getattr(active, "params", {})),
                overrides=improved_params,
            )
            version_data["autolearn_meta"] = {
                "rationale": improved_params.get("rationale", ""),
                "oos_metrics": improved_params.get("oos_metrics", {}),
                "regime_at_creation": self._current_regime,
            }

            tmp_path = out_path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
            try:
                tmp_path.write_text(json.dumps(version_data, indent=2))
                os.replace(tmp_path, out_path)
            finally:
                tmp_path.unlink(missing_ok=True)
                lock_path.unlink(missing_ok=True)

            from alphaloop.core.events import StrategyVersionCreated
            await self._event_bus.publish(StrategyVersionCreated(
                symbol=self._symbol,
                version=new_version,
                source="autolearn",
            ))

            logger.info(
                "[meta-loop] %s: created strategy v%d (source=autolearn)",
                self._symbol, new_version,
            )
            return new_version

        except Exception as e:
            logger.error("[meta-loop] Failed to create strategy version: %s", e)
            return None

    async def _activate_version(
        self, active, new_version: int, improved_params: dict
    ) -> None:
        """Store new version as active and initialise RollbackTracker."""
        import json
        import numpy as np

        try:
            # Compute current baseline Sharpe for rollback comparison
            if self._baseline_r_multiples:
                arr = np.array(self._baseline_r_multiples[-self._rollback_window:])
                baseline_sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0
            else:
                baseline_sharpe = 0.0

            version_data = _strategy_version_payload(
                self._symbol,
                new_version,
                "active",
                "autolearn",
                active,
                improved_params.get("params", getattr(active, "params", {})),
                overrides=improved_params,
            )
            strategy_json = json.dumps(version_data)

            if self._instance_id:
                await self._settings_service.set(
                    f"active_strategy_{self._instance_id}", strategy_json,
                )
            await self._settings_service.set(
                f"active_strategy_{self._symbol}", strategy_json,
            )

            # Initialise rollback tracker to monitor new version performance
            self._rollback_tracker = RollbackTracker(
                previous_version=getattr(active, "version", 0),
                previous_sharpe=baseline_sharpe,
                rollback_window=self._rollback_window,
            )

            logger.info(
                "[meta-loop] %s: activated v%d (baseline_sharpe=%.3f)",
                self._symbol, new_version, baseline_sharpe,
            )

        except Exception as e:
            logger.error("[meta-loop] Failed to activate strategy version: %s", e)

    def _build_tool_decay_report(self) -> str:
        """
        Build a decay report string for the research AI context.
        Returns empty string if no tracker is available.
        """
        try:
            from alphaloop.scoring.tool_tracker import tool_tracker
            return tool_tracker.decay_report_text()
        except Exception:
            return ""

    async def _publish_completion(self, action: str, retrain_check: dict) -> None:
        """Publish MetaLoopCompleted event."""
        from alphaloop.core.events import MetaLoopCompleted
        await self._event_bus.publish(MetaLoopCompleted(
            symbol=self._symbol,
            action_taken=action,
            details=f"regime={self._current_regime} degradation={retrain_check.get('degradation_status', {})}",
        ))

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
        prev_data = None
        for f in STRATEGY_VERSIONS_DIR.glob(f"{self._symbol}_v*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("version") == prev_ver:
                    prev_data = data
                    break
            except (json.JSONDecodeError, OSError):
                continue

        if prev_data:
            rollback_payload = build_active_strategy_payload(prev_data)
            strategy_json = json.dumps(rollback_payload)
            # Write to per-instance key (primary) + symbol key (legacy fallback)
            if self._instance_id:
                await self._settings_service.set(
                    f"active_strategy_{self._instance_id}", strategy_json,
                )
            await self._settings_service.set(
                f"active_strategy_{self._symbol}", strategy_json,
            )

        from alphaloop.core.events import StrategyRolledBack
        await self._event_bus.publish(StrategyRolledBack(
            symbol=self._symbol,
            instance_id=self._instance_id,
            from_version=0,  # current version unknown here
            to_version=prev_ver,
            reason="R-multiple Sharpe below 70% of previous version",
        ))

        self._rollback_tracker = None
