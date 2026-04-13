"""
Core async trading cycle — ~300 lines replacing v2's 2,893-line main.py.

Each cycle:
  1. Check risk monitor (kill switch, daily limits)
  2. Build market context (data + indicators)
  3. Run filter pipeline (session, news, volatility, etc.)
  4. Generate signal via AI
  5. Validate signal (hard rules + AI)
  6. Size position
  7. Execute order (MT5 or dry-run)
  8. Log to DB + notify
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from alphaloop.core.events import (
    EventBus,
    CycleStarted,
    CycleCompleted,
    PipelineStep,
    SignalGenerated,
    SignalValidated,
    SignalRejected,
    TradeOpened,
    PipelineBlocked,
    TradeRepositioned,
)
from alphaloop.core.types import ValidationStatus
from alphaloop.risk.guards import (
    NearDedupGuard,
    PortfolioCapGuard,
    SignalHashFilter,
    ConfidenceVarianceFilter,
    SpreadRegimeFilter,
    EquityCurveScaler,
    DrawdownPauseGuard,
)
from alphaloop.signals.schema import TradeSignal, ValidatedSignal
from alphaloop.trading.circuit_breaker import CircuitBreaker
from alphaloop.trading.heartbeat import HeartbeatWriter
from alphaloop.risk.cross_instance import CrossInstanceRiskAggregator
from alphaloop.risk.guard_persistence import load_guard_state, save_guard_state
from alphaloop.execution.control_plane import InstitutionalControlPlane
from alphaloop.execution.service import ExecutionService
from alphaloop.trading.runtime_utils import (
    current_account_balance,
    current_runtime_strategy,
    current_strategy_reference,
    safe_json_payload,
    session_name_from_context,
)
from alphaloop.trading.strategy_loader import (
    resolve_algorithmic_setup_tag,
)

logger = logging.getLogger(__name__)

class TradingLoop:
    """
    Async trading loop orchestrator.
    Ties together signal engine, validator, sizer, executor, and risk monitor.
    """

    def __init__(
        self,
        *,
        symbol: str = "XAUUSD",
        instance_id: str = "",
        poll_interval: float = 300.0,
        dry_run: bool = True,
        event_bus: EventBus | None = None,
        signal_engine=None,
        sizer=None,
        executor=None,
        risk_monitor=None,
        trade_repo=None,
        notifier=None,
        ai_caller=None,
        signal_model_id: str = "",
        settings_service=None,
        tool_registry=None,
        session_factory=None,
        supervision_service=None,
        redis_sync=None,
    ):
        self.symbol = symbol
        self.instance_id = instance_id
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.event_bus = event_bus or EventBus()

        # Data fetcher for live candle data
        self._fetcher = None

        # Injected components
        self.signal_engine = signal_engine
        self.sizer = sizer
        self.executor = executor
        self.risk_monitor = risk_monitor
        self.trade_repo = trade_repo
        self.notifier = notifier
        self.ai_caller = ai_caller
        self.signal_model_id = signal_model_id
        self.settings_service = settings_service
        self.tool_registry = tool_registry
        self._session_factory = session_factory
        self._supervision_service = supervision_service
        self._redis_sync = redis_sync

        # Redis cross-session learning modules (initialized if Redis is available)
        self._redis_regime = None     # RedisRegimePersistence
        self._redis_guards = None     # RedisGuardPersistence
        self._redis_ece = None        # RedisECEPersistence
        if redis_sync and getattr(redis_sync, "_client", None):
            from alphaloop.pipeline.redis_regime import RedisRegimePersistence
            from alphaloop.risk.redis_guards import RedisGuardPersistence
            from alphaloop.pipeline.redis_ece import RedisECEPersistence
            _rc = redis_sync._client
            _iid = getattr(redis_sync, "_instance_id", "default")
            self._redis_regime = RedisRegimePersistence(_rc, instance_id=_iid)
            self._redis_guards = RedisGuardPersistence(_rc)
            self._redis_ece = RedisECEPersistence(_rc, instance_id=_iid)

        self._running = False
        self._halt_trading = False  # Phase 2F: set True on critical persistence failure
        self._circuit = CircuitBreaker()
        self._heartbeat = HeartbeatWriter()
        self._cycle_count = 0

        # ── Phase 0C: Remediation banner ─────────────────────────────────────
        # ─────────────────────────────────────────────────────────────────────

        # Strategy-driven state (loaded from DB each cycle)
        self._active_strategy = None       # ActiveStrategyConfig | None
        self._runtime_strategy: dict[str, Any] = {}
        self._feature_pipeline = None      # FeaturePipeline | None (algo_ai)
        self._algo_engine = None           # AlgorithmicSignalEngine | None
        self._strategy_runtime_sig = ""
        self._v4_orchestrator = None       # Cached PipelineOrchestrator
        self._asset_tf_overrides: dict[str, dict] = {}  # DB per-TF overrides

        # Stateful guards (persist across cycles)
        self._signal_hash = SignalHashFilter(window=3)
        self._conf_variance = ConfidenceVarianceFilter(window=3, max_stdev=0.15)
        self._spread_regime = SpreadRegimeFilter(window=50, threshold=1.8)
        self._equity_scaler = EquityCurveScaler(window=20)
        self._dd_pause = DrawdownPauseGuard(pause_minutes=30)
        self._near_dedup = NearDedupGuard(min_atr_distance=1.0)
        self._portfolio_cap = PortfolioCapGuard(max_portfolio_risk_pct=6.0)

        # S-03: Cached RegimeClassifier — holds EWM smoothed state across cycles.
        # Rebuilt only when tools change (strategy reload).  Injected into each
        # orchestrator instead of constructing a fresh instance per cycle.
        from alphaloop.pipeline.regime import RegimeClassifier as _RegimeClassifier
        self._regime_classifier = _RegimeClassifier()

        # Trade repositioner (evaluates open trades each cycle)
        from alphaloop.risk.repositioner import TradeRepositioner
        self._repositioner = TradeRepositioner()

        # Trailing stop loss manager
        from alphaloop.risk.trailing_manager import TrailingStopManager
        self._trailing_manager = TrailingStopManager()

        # Canary state (loaded from DB settings)
        self._canary_allocation: float | None = None  # e.g. 0.10 = 10%

        # Cross-instance risk aggregator (reads all instances' trades from shared DB)
        self._cross_risk = CrossInstanceRiskAggregator(trade_repo=trade_repo)
        self._control_plane = InstitutionalControlPlane(
            session_factory=session_factory,
            cross_risk=self._cross_risk,
            dry_run=dry_run,
        )
        self._execution_service = ExecutionService(
            session_factory=session_factory,
            executor=executor,
            control_plane=self._control_plane,
            supervision_service=supervision_service,
            dry_run=dry_run,
        )

        # ── Extracted services ────────────────────────────────────────────────
        from alphaloop.trading.signal_dispatcher import SignalDispatcher
        from alphaloop.trading.execution_orchestrator import ExecutionOrchestrator

        self._signal_dispatcher = SignalDispatcher(
            signal_engine=signal_engine,
            ai_caller=ai_caller,
            symbol=symbol,
            instance_id=instance_id,
        )
        self._execution_orch = ExecutionOrchestrator(
            sizer=sizer,
            execution_service=self._execution_service,
            event_bus=self.event_bus,
            symbol=symbol,
            instance_id=instance_id,
            dry_run=dry_run,
            risk_monitor=risk_monitor,
            notifier=notifier,
            settings_service=settings_service,
            guard_state_refs={
                "hash_filter": self._signal_hash,
                "conf_variance": self._conf_variance,
                "spread_regime": self._spread_regime,
                "equity_scaler": self._equity_scaler,
                "dd_pause": self._dd_pause,
            },
        )

    async def run(self) -> None:
        """Main loop — runs until stopped."""
        self._running = True
        logger.info(
            "Trading loop started | symbol=%s | instance=%s | dry_run=%s",
            self.symbol, self.instance_id, self.dry_run,
        )

        # Restore guard state from DB (survives restarts)
        if self.settings_service:
            await load_guard_state(
                self.settings_service,
                hash_filter=self._signal_hash,
                conf_variance=self._conf_variance,
                spread_regime=self._spread_regime,
                equity_scaler=self._equity_scaler,
                dd_pause=self._dd_pause,
            )

        # Restore Redis cross-session learning state (faster than DB seed)
        try:
            if self._redis_regime:
                await self._redis_regime.pull_state(self._regime_classifier, self.symbol)
            if self._redis_guards:
                await self._redis_guards.pull_state(
                    self._signal_hash, self._conf_variance, self.symbol,
                )
        except Exception as e:
            logger.warning("[loop] Redis state restore failed (non-critical): %s", e)

        while self._running:
            try:
                await self._cycle()
                self._circuit.record_success()
            except Exception as e:
                logger.error("Trading cycle error: %s", e, exc_info=True)
                self._circuit.record_failure()
                if self._circuit.should_kill and self.risk_monitor:
                    logger.critical("Circuit breaker kill threshold — activating kill switch")
                    self.risk_monitor.activate_kill_switch(
                        "Circuit breaker kill threshold reached"
                    )
                    self._running = False

            self._heartbeat.write({
                "symbol": self.symbol,
                "cycle": self._cycle_count,
                "circuit_breaker": self._circuit.status,
                "risk": self.risk_monitor.status if self.risk_monitor else {},
            })

            if self._running:
                await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False
        logger.info("Trading loop stop requested")

    def _active_strategy_runtime(self) -> dict[str, Any]:
        if self._runtime_strategy:
            return dict(self._runtime_strategy)
        return current_runtime_strategy(active_strategy=self._active_strategy)

    async def _submit_execution(
        self,
        *,
        signal: Any,
        sizing: dict,
        stop_loss: float,
        take_profit: float,
        take_profit_2: float | None = None,
        comment: str = "",
        validated: Any | None = None,
        context: Any = None,
    ):
        execution_sizing = dict(sizing)
        if "risk_amount_usd" not in execution_sizing and "risk_usd" in execution_sizing:
            execution_sizing["risk_amount_usd"] = execution_sizing.get("risk_usd")
        runtime_strategy = self._active_strategy_runtime()
        strategy_ref = current_strategy_reference(
            symbol=self.symbol,
            runtime_strategy=runtime_strategy,
        )
        return await self._execution_service.execute_market_order(
            symbol=self.symbol,
            instance_id=self.instance_id,
            account_balance=current_account_balance(
                risk_monitor=self.risk_monitor,
                sizer=self.sizer,
            ),
            signal=signal,
            sizing=execution_sizing,
            stop_loss=stop_loss,
            take_profit=take_profit,
            take_profit_2=take_profit_2,
            comment=comment,
            strategy_id=strategy_ref["strategy_id"],
            strategy_version=strategy_ref["strategy_version"],
            signal_payload=safe_json_payload(signal),
            validation_payload=safe_json_payload(validated),
            market_context_snapshot=safe_json_payload(
                {"symbol": self.symbol, "session": session_name_from_context(context)}
            ),
            session_name=session_name_from_context(context),
            is_dry_run=self.dry_run,
        )

    async def _ensure_strategy_loaded(self) -> None:
        """Load active strategy from DB and build pipeline if version changed."""
        if not self.settings_service or not self.tool_registry:
            return

        from alphaloop.trading.strategy_loader import (
            load_active_strategy, build_feature_pipeline,
        )

        config = await load_active_strategy(self.settings_service, self.symbol, self.instance_id)
        if config is None:
            self._active_strategy = None
            self._runtime_strategy = {}
            self._feature_pipeline = None
            self._algo_engine = None
            self._strategy_runtime_sig = ""
            self.signal_model_id = ""
            self._v4_orchestrator = None
            self._signal_dispatcher.update_algo_engine(None)
            self._signal_dispatcher.update_signal_model("")
            self._execution_orch.update_state(
                active_strategy=None,
                runtime_strategy=None,
                canary_allocation=self._canary_allocation,
            )
            return

        runtime_config = current_runtime_strategy(active_strategy=config)
        next_runtime_sig = json.dumps(runtime_config, sort_keys=True, default=str)

        # Only rebuild if the effective runtime strategy contract changed.
        if self._active_strategy and self._strategy_runtime_sig == next_runtime_sig:
            return

        self._active_strategy = config

        # Strategy changed — invalidate cached orchestrator so it rebuilds
        self._v4_orchestrator = None

        # Build feature pipeline for algo_ai mode
        self._runtime_strategy = dict(runtime_config)
        self._strategy_runtime_sig = next_runtime_sig
        runtime_signal_mode = str(runtime_config.get("signal_mode") or "")

        if runtime_signal_mode == "algo_ai":
            self._feature_pipeline = build_feature_pipeline(config, self.tool_registry)
        else:
            self._feature_pipeline = None

        # Update signal model from strategy's AI models
        runtime_ai_models = dict(runtime_config.get("ai_models") or {})
        self.signal_model_id = str(runtime_ai_models.get("signal") or "")

        # Create algorithmic engine with strategy params
        from alphaloop.signals.algorithmic import AlgorithmicSignalEngine
        prev_state = None
        if self._algo_engine:
            prev_state = {
                "fast": self._algo_engine._prev_fast,
                "slow": self._algo_engine._prev_slow,
            }
        self._algo_engine = AlgorithmicSignalEngine(
            self.symbol,
            dict(runtime_config.get("params") or {}),
            prev_ema_state=prev_state,
            setup_tag=resolve_algorithmic_setup_tag(runtime_config),
        )

        # Notify extracted services of updated strategy state
        self._signal_dispatcher.update_algo_engine(self._algo_engine)
        self._signal_dispatcher.update_signal_model(self.signal_model_id)
        self._execution_orch.update_state(
            active_strategy=self._active_strategy,
            runtime_strategy=self._runtime_strategy,
            canary_allocation=self._canary_allocation,
        )

        logger.info(
            "[loop] Loaded strategy %s v%d (mode=%s, setup_family=%s, tools=%d)",
            runtime_config.get("symbol", config.symbol),
            int(runtime_config.get("version", config.version) or 0),
            runtime_signal_mode,
            runtime_config.get("setup_family", ""),
            sum(1 for v in dict(runtime_config.get("tools") or {}).values() if v),
        )

    async def _cycle(self) -> None:
        """Single trading cycle."""
        self._cycle_count += 1
        t0 = time.time()

        # Create per-cycle trade_repo with fresh DB session
        if self._session_factory and not self.trade_repo:
            self._cycle_session = self._session_factory()
            from alphaloop.db.repositories.trade_repo import TradeRepository
            self.trade_repo = TradeRepository(self._cycle_session)
        else:
            self._cycle_session = None

        try:
            await self._cycle_inner(t0)
        finally:
            # Record cycle duration metric (every cycle, success or failure)
            from alphaloop.monitoring.metrics import metrics_tracker as _mt
            _mt.record_sync("cycle_duration_ms", (time.time() - t0) * 1000)

            # Push state to Redis every 10 cycles (HA cache + cross-session learning)
            if self._redis_sync and self._cycle_count % 10 == 0:
                try:
                    if self.risk_monitor:
                        await self._redis_sync.push_risk_state(self.risk_monitor)
                    if self._redis_regime and hasattr(self, "_regime_classifier"):
                        await self._redis_regime.push_state(self._regime_classifier, self.symbol)
                    if self._redis_guards:
                        await self._redis_guards.push_state(
                            self._signal_hash, self._conf_variance, self.symbol,
                        )
                except Exception:
                    pass  # Redis is non-critical

            # Close the per-cycle session
            if self._cycle_session:
                try:
                    await self._cycle_session.commit()
                except Exception:
                    await self._cycle_session.rollback()
                finally:
                    await self._cycle_session.close()
                    self._cycle_session = None
                    # Reset so next cycle creates a fresh session
                    if self._session_factory:
                        self.trade_repo = None

    async def _cycle_inner(self, t0: float) -> None:
        """Inner cycle logic with proper session management."""

        # Phase 2F: Halt trading if a critical persistence failure occurred
        if self._halt_trading:
            logger.critical(
                "[v4] Trading HALTED due to prior critical persistence failure. "
                "Manual intervention required."
            )
            return

        # Announce cycle start so the WebUI raw log shows activity immediately
        await self.event_bus.publish(CycleStarted(
            symbol=self.symbol,
            instance_id=self.instance_id,
            cycle=self._cycle_count,
        ))

        # 0. Cross-instance portfolio snapshot (approval happens after final sizing)
        balance = 0.0
        if self.sizer:
            balance = getattr(self.sizer, "account_balance", 0.0)
        # Update cross-risk with current cycle's trade_repo
        self._cross_risk.trade_repo = self.trade_repo
        cross_status = await self._cross_risk.get_aggregate_status(balance)
        if not cross_status.get("available", True):
            logger.warning(
                "[cycle] Cross-instance snapshot unavailable at pre-check: %s",
                cross_status.get("reason", ""),
            )

        # 1. Risk pre-check
        if self.risk_monitor:
            can_trade, reason = await self.risk_monitor.can_open_trade()
            if not can_trade:
                logger.info("[cycle] Blocked by risk monitor: %s", reason)
                await self.event_bus.publish(PipelineBlocked(
                    symbol=self.symbol,
                    reason=reason,
                    blocked_by="risk_monitor",
                ))
                await self._publish_cycle_done("blocked", reason)
                return

        # Risk checks passed — emit step
        risk_detail = ""
        if self.risk_monitor:
            risk_detail = f"daily:${self.risk_monitor._daily_pnl:+.0f} | consec:{self.risk_monitor._consecutive_losses} | open:{self.risk_monitor._open_trades}"
        await self._publish_step("risk_check", "passed", risk_detail)

        # 2. Circuit breaker
        if self._circuit.is_open:
            logger.info("[cycle] Circuit breaker open — skipping")
            await self.event_bus.publish(PipelineBlocked(
                symbol=self.symbol,
                reason="circuit breaker open",
                blocked_by="circuit_breaker",
            ))
            await self._publish_cycle_done("blocked", "circuit breaker open")
            return

        # 2b. Load active strategy from DB (hot-reloads on version change)
        await self._ensure_strategy_loaded()

        # 3. Build market context (placeholder — data layer provides this)
        context = await self._build_context()

        # Determine signal mode early
        signal_mode = str(self._active_strategy_runtime().get("signal_mode") or "")

        # ── v4 institutional pipeline (all modes) ──
        await self._cycle_v4(context, signal_mode, t0)

        # ── Position monitoring — repositioner + trailing SL (all modes) ──
        await self._reposition_open_trades(context)
        return

    @staticmethod
    def _summarize_stage_result(result: dict) -> str:
        tool_summaries = []
        for item in result.get("results", []):
            status = "pass" if item.get("passed", True) else "BLOCK"
            tool_summaries.append(f"{item.get('tool_name', '?')}:{status}")
        summary = " | ".join(tool_summaries)
        suffix = f"size:{result.get('size_modifier', 1.0):.2f} bias:{result.get('bias', 'neutral')}"
        return f"{summary} | {suffix}" if summary else suffix

    # ------------------------------------------------------------------
    # v4 institutional pipeline
    # ------------------------------------------------------------------

    def _get_correlation_guard(self):
        """Get the CorrelationGuard plugin instance from the tool registry."""
        if self.tool_registry:
            try:
                return self.tool_registry.get_tool("correlation_guard")
            except Exception:
                pass
        return None

    def _get_stage_tools(self, stage: str) -> list:
        """Return enabled plugin instances for a given pipeline stage.

        Filters by:
          1. stage assignment (STAGE_TOOL_MAP)
          2. strategy card toggle (active_strategy.tools dict, default=enabled)
        """
        from alphaloop.tools.registry import STAGE_TOOL_MAP
        if not self.tool_registry or not self._active_strategy:
            return []
        runtime_strategy = self._active_strategy_runtime()
        active_tools = dict(runtime_strategy.get("tools") or {})
        names = STAGE_TOOL_MAP.get(stage, [])
        result = []
        for name in names:
            if active_tools.get(name, True):
                tool = self.tool_registry.get_tool(name)
                if tool is not None:
                    result.append(tool)
        return result

    def _apply_tools_config(self, asset_cfg, active_tf: str, runtime_strategy: dict) -> None:
        """Apply TF-calibrated + strategy-level config to each registered tool."""
        from alphaloop.config.asset_classes import merge_tools_config

        strategy_tc = dict((runtime_strategy.get("tools_config") or {}))
        merged = merge_tools_config(asset_cfg.asset_class, {})

        tf_defaults = (getattr(asset_cfg, "default_params_by_timeframe", None) or {}).get(
            active_tf, {}
        )
        tf_tools_cfg = tf_defaults.get("tools_config") or {}
        for plugin, plugin_params in tf_tools_cfg.items():
            if plugin in merged:
                merged[plugin] = {**merged[plugin], **plugin_params}
            else:
                merged[plugin] = dict(plugin_params)

        db_tf_tools = (
            (getattr(self, "_asset_tf_overrides", None) or {}).get(active_tf) or {}
        ).get("tools_config") or {}
        for plugin, plugin_params in db_tf_tools.items():
            if plugin in merged:
                merged[plugin] = {**merged[plugin], **plugin_params}
            else:
                merged[plugin] = dict(plugin_params)

        for plugin, plugin_params in strategy_tc.items():
            if plugin in merged:
                merged[plugin] = {**merged[plugin], **plugin_params}
            else:
                merged[plugin] = dict(plugin_params)

        if self.tool_registry:
            for plugin_name, plugin_cfg in merged.items():
                tool = self.tool_registry.get_tool(plugin_name)
                if tool is not None:
                    tool.configure(plugin_cfg)

    def _build_v4_orchestrator(self):
        """Lazily build the v4 PipelineOrchestrator from existing components."""
        from alphaloop.pipeline.orchestrator import PipelineOrchestrator
        from alphaloop.pipeline.market_gate import MarketGate
        from alphaloop.pipeline.regime import RegimeClassifier
        from alphaloop.pipeline.invalidation import StructuralInvalidator
        from alphaloop.pipeline.quality import StructuralQuality
        from alphaloop.pipeline.conviction import ConvictionScorer
        from alphaloop.pipeline.execution_guard import ExecutionGuardRunner
        from alphaloop.pipeline.risk_gate import RiskGateRunner
        from alphaloop.pipeline.defaults import load_pipeline_config

        # Load pipeline config (defaults + strategy overrides)
        runtime_strategy = self._active_strategy_runtime()
        strat_validation = dict(runtime_strategy.get("validation") or {})
        cfg = load_pipeline_config(strat_validation)

        # Resolve construction params through 5-layer precedence
        from alphaloop.config.assets import get_asset_config as _get_asset
        from alphaloop.trading.strategy_loader import resolve_construction_params
        _asset_cfg = _get_asset(self.symbol)
        active_tf = str(
            (self._runtime_strategy or {}).get("params", {}).get("timeframe", "M15")
        ).upper()
        _cp = resolve_construction_params(
            runtime_strategy, active_tf, _asset_cfg,
            tf_db_overrides=getattr(self, "_asset_tf_overrides", None),
        )

        # Override invalidation SL bounds with resolved values
        cfg["invalidation"]["sl_min_points"] = _cp["sl_min_points"]
        cfg["invalidation"]["sl_max_points"] = _cp["sl_max_points"]
        cfg["invalidation"]["pip_size"] = _asset_cfg.pip_size

        # Build TradeConstructor with fully resolved params
        from alphaloop.pipeline.construction import TradeConstructor
        _tc = TradeConstructor(
            pip_size=_asset_cfg.pip_size,
            sl_min_pts=_cp["sl_min_points"],
            sl_max_pts=_cp["sl_max_points"],
            tp1_rr=_cp["tp1_rr"],
            tp2_rr=_cp["tp2_rr"],
            entry_zone_atr_mult=_cp["entry_zone_atr_mult"],
            sl_buffer_atr=_cp["sl_buffer_atr"],
            sl_atr_mult=_cp["sl_atr_mult"],
            tools=self._get_stage_tools("construction"),
        )

        # Configure tools with TF-calibrated params (BUG-3 fix)
        self._apply_tools_config(_asset_cfg, active_tf, runtime_strategy)

        # Per-stage tool injection — each stage gets only its assigned plugins,
        # filtered by the strategy card toggle (active_strategy.tools dict).
        _active_tools = dict(runtime_strategy.get("tools") or {})

        # S-03: Inject tools into the cached RegimeClassifier (tools may change
        # on strategy reload but the EWM smoothed state is preserved).
        self._regime_classifier._tools = self._get_stage_tools("regime")

        return PipelineOrchestrator(
            market_gate=MarketGate(
                **cfg["market_gate"],
                tools=self._get_stage_tools("market_gate"),
            ),
            regime_classifier=self._regime_classifier,
            trade_constructor=_tc,
            invalidator=StructuralInvalidator(
                cfg=cfg["invalidation"],
                tools=self._get_stage_tools("invalidation"),
            ),
            quality_scorer=StructuralQuality(
                tools=self._get_stage_tools("quality"),
            ),
            conviction_scorer=ConvictionScorer(
                strategy_params=runtime_strategy or None,
                max_penalty=cfg["conviction"]["max_total_conviction_penalty"],
            ),
            risk_gate=RiskGateRunner(
                equity_curve_scaler=self._equity_scaler,
                drawdown_pause_guard=self._dd_pause,
                portfolio_cap_guard=self._portfolio_cap,
                correlation_guard=self.tool_registry.get_tool("correlation_guard")
                    if self.tool_registry and _active_tools.get("correlation_guard", True) else None,
                risk_filter_tool=self.tool_registry.get_tool("risk_filter")
                    if self.tool_registry and _active_tools.get("risk_filter", True) else None,
            ),
            execution_guard=ExecutionGuardRunner(
                signal_hash_filter=self._signal_hash,
                confidence_variance_filter=self._conf_variance,
                spread_regime_filter=self._spread_regime,
                near_dedup_guard=self._near_dedup,
                tick_jump_tool=self.tool_registry.get_tool("tick_jump_guard")
                    if self.tool_registry and _active_tools.get("tick_jump_guard", True) else None,
                liq_vacuum_tool=self.tool_registry.get_tool("liq_vacuum_guard")
                    if self.tool_registry and _active_tools.get("liq_vacuum_guard", True) else None,
                tick_jump_atr_max=cfg["execution"]["tick_jump_atr_max"],
                liq_vacuum_spike_mult=cfg["execution"]["liq_vacuum_spike_mult"],
                liq_vacuum_body_pct=cfg["execution"]["liq_vacuum_body_pct"],
                max_delay_candles=cfg["execution"]["max_delay_candles"],
            ),
            hypothesis_tools=self._get_stage_tools("hypothesis"),
            enabled_tools=_active_tools,
        )

    async def _log_pipeline_decision(self, result, *, signal_mode: str = "algo_only") -> None:
        """S-06: Persist one PipelineDecision (+ RejectionLog if blocked) to DB.

        Gate-1: also writes one PipelineStageDecision row per journey stage into
        the ``pipeline_stage_decisions`` table so the observability funnel
        endpoint can query pass/reject counts per stage without unpacking the
        legacy ``tool_results.journey`` JSON blob.

        Fire-and-forget — caller wraps in try/except so failures never block trading.
        """
        if not self._session_factory:
            return

        from alphaloop.db.models.pipeline import (
            PipelineDecision,
            PipelineStageDecision,
            RejectionLog,
        )
        from alphaloop.pipeline.types import CycleOutcome, build_trade_decision

        direction = None
        if result.signal:
            direction = result.signal.direction
        elif result.hypothesis:
            direction = result.hypothesis.direction

        allowed = result.outcome == CycleOutcome.TRADE_OPENED
        block_reason = result.rejection_reason
        size_modifier: float | None = None
        if result.risk_gate:
            size_modifier = result.risk_gate.size_modifier
        journey_payload = result.journey.to_dict() if getattr(result, "journey", None) else None

        # Determine which stage blocked the trade
        blocked_by: str | None = None
        if not allowed:
            if result.market_gate and not result.market_gate.tradeable:
                blocked_by = "market_gate"
            elif result.outcome == CycleOutcome.NO_SIGNAL:
                blocked_by = "no_signal"
            elif result.outcome == CycleOutcome.NO_CONSTRUCTION:
                blocked_by = "no_construction"
            elif result.invalidation and result.invalidation.severity == "HARD_INVALIDATE":
                blocked_by = "invalidation"
            elif result.conviction and result.conviction.decision == "HOLD":
                blocked_by = "conviction"
            elif result.risk_gate and not result.risk_gate.allowed:
                blocked_by = "risk_gate"
            elif result.execution_guard and result.execution_guard.action == "BLOCK":
                blocked_by = "execution_guard"
            elif result.execution_guard and result.execution_guard.action == "DELAY":
                blocked_by = "execution_guard_delay"
            else:
                blocked_by = "pipeline"

        # Build the TradeDecision projection once and reuse it for per-stage rows.
        decision = build_trade_decision(result, symbol=self.symbol, mode=signal_mode)
        cycle_id = f"{self.instance_id or 'loop'}-{int(decision.occurred_at.timestamp() * 1000)}"

        async with self._session_factory() as session:
            dec = PipelineDecision(
                symbol=self.symbol,
                direction=direction,
                allowed=allowed,
                blocked_by=blocked_by,
                block_reason=block_reason,
                size_modifier=size_modifier,
                tool_results={
                    "journey": journey_payload,
                    "construction_source": result.construction_source,
                    "trade_decision": decision.to_dict(),
                } if journey_payload or result.construction_source else None,
                instance_id=self.instance_id,
            )
            session.add(dec)

            # RejectionLog: only when a signal was fully constructed then blocked
            if not allowed and direction and result.signal:
                rej = RejectionLog(
                    symbol=self.symbol,
                    direction=direction,
                    setup_type=result.signal.setup_type,
                    session_name=result.regime.regime if result.regime else None,
                    rejected_by=blocked_by,
                    reason=block_reason,
                    instance_id=self.instance_id,
                )
                session.add(rej)

            # Per-stage funnel ledger — one row per CandidateJourneyStage.
            journey = getattr(result, "journey", None)
            if journey is not None and journey.stages:
                for idx, stage in enumerate(journey.stages):
                    session.add(
                        PipelineStageDecision(
                            occurred_at=decision.occurred_at,
                            cycle_id=cycle_id,
                            source="live",
                            symbol=self.symbol,
                            instance_id=self.instance_id,
                            mode=signal_mode,
                            stage=stage.stage,
                            stage_index=idx,
                            status=stage.status,
                            blocked_by=stage.blocked_by,
                            detail=(stage.detail or "")[:2000] or None,
                            payload=stage.payload or None,
                            outcome=decision.outcome,
                            reject_stage=decision.reject_stage,
                            direction=decision.direction,
                            setup_type=decision.setup_type,
                            conviction_score=decision.conviction_score,
                            size_multiplier=decision.size_multiplier,
                            latency_ms=decision.latency_ms,
                        )
                    )

            await session.commit()

    async def _cycle_v4(self, context, signal_mode: str, t0: float) -> None:
        """
        Run the full v4 institutional pipeline as the primary execution path.

        Replaces _cycle_inner / _cycle_algo_ai for pipeline_version='v4'.
        """
        if not hasattr(self, "_v4_orchestrator") or self._v4_orchestrator is None:
            self._v4_orchestrator = self._build_v4_orchestrator()
        orchestrator = self._v4_orchestrator

        # Check for delayed signals from previous cycles first
        delayed_result = await orchestrator.check_delayed(context, self.symbol)
        if delayed_result is not None:
            from alphaloop.pipeline.types import CycleOutcome
            if delayed_result.outcome == CycleOutcome.TRADE_OPENED and delayed_result.signal:
                logger.info(
                    "[v4] Executing delayed signal: %s %s (freshness=%.3f)",
                    delayed_result.signal.direction,
                    delayed_result.signal.setup_type,
                    delayed_result.sizing.freshness_scalar if delayed_result.sizing else 0,
                )
                await self._execute_v4_trade(delayed_result, context, t0)
                return
            elif delayed_result.outcome == CycleOutcome.REJECTED:
                logger.info("[v4] Delayed signal rejected: %s", delayed_result.rejection_reason)
                # Fall through to generate a new signal

        # Phase 4D: Wire AI validator for both algo_ai and ai_signal modes
        # Invariant 6: no AI-originated trade bypasses deterministic validation
        if signal_mode in ("algo_ai", "ai_signal") and self.ai_caller:
            from alphaloop.pipeline.ai_validator import BoundedAIValidator
            runtime_strategy = self._active_strategy_runtime()
            validator_model = dict(runtime_strategy.get("ai_models") or {}).get("validator", "")
            orchestrator.ai_validator = BoundedAIValidator(
                ai_caller=self.ai_caller,
                validator_model=validator_model,
                validator_instruction=str(runtime_strategy.get("validator_instruction") or ""),
                fail_open=False,
            )

        # Build signal generator closure — delegates to SignalDispatcher
        # (trade construction happens in orchestrator Stage 3B)
        async def generate_signal(ctx, regime):
            return await self._signal_dispatcher.dispatch(
                ctx, regime,
                signal_mode=signal_mode,
                active_strategy=self._active_strategy,
                runtime_strategy=self._active_strategy_runtime(),
            )

        result = await orchestrator.run(
            context,
            generate_signal,
            symbol=self.symbol,
            mode=signal_mode,
        )

        # Record waterfall for WebUI (every cycle, regardless of outcome)
        try:
            from alphaloop.webui.routes.event_log import record_waterfall
            record_waterfall(result)
        except Exception:
            pass

        # S-06: Persist pipeline decision to DB for Tools tab (fire-and-forget)
        try:
            await self._log_pipeline_decision(result, signal_mode=signal_mode)
        except Exception:
            pass

        # Publish v4 pipeline events to the event bus
        from alphaloop.pipeline.types import CycleOutcome

        # Stage-by-stage events for the event log timeline
        if result.market_gate:
            detail = "tradeable" if result.market_gate.tradeable else result.market_gate.block_reason
            await self._publish_step("market_gate", "passed" if result.market_gate.tradeable else "blocked", detail or "")

        if result.regime:
            await self._publish_step(
                "regime",
                "classified",
                f"{result.regime.regime} | session={result.regime.session_quality:.2f} | "
                f"setups={result.regime.allowed_setups}",
            )

        # Emit hypothesis event if one was produced
        if result.hypothesis:
            from alphaloop.core.events import DirectionHypothesized
            await self._publish_step(
                "hypothesis", "generated",
                f"direction hypothesis: {result.hypothesis.direction} "
                f"confidence={result.hypothesis.confidence:.2f}",
                context=result.hypothesis.source_detail or {},
            )
            await self.event_bus.publish(DirectionHypothesized(
                symbol=self.symbol,
                direction=result.hypothesis.direction,
                confidence=result.hypothesis.confidence,
                setup_tag=result.hypothesis.setup_tag,
                source_names=result.hypothesis.source_names,
            ))

        # Emit construction events
        if result.outcome == CycleOutcome.NO_CONSTRUCTION and result.hypothesis:
            from alphaloop.core.events import ConstructionFailed
            _construction_ctx = {
                "entry": getattr(self._constructor, "_last_entry", 0),
                "direction": result.hypothesis.direction,
                "candidates": getattr(self._constructor, "_last_rejection_details", []),
                "candidates_considered": getattr(self._constructor, "_last_candidates", 0),
                "sl_min_pts": getattr(self._constructor, "_sl_min", None),
                "sl_max_pts": getattr(self._constructor, "_sl_max", None),
            }
            await self._publish_step(
                "construction", "no_structure",
                result.rejection_reason or "no valid SL from structure",
                context=_construction_ctx,
            )
            await self.event_bus.publish(ConstructionFailed(
                symbol=self.symbol,
                direction=result.hypothesis.direction,
                reason=result.rejection_reason or "",
                candidates_considered=0,
            ))

        if result.signal:
            # Emit construction success + SignalGenerated
            if result.construction_source:
                from alphaloop.core.events import TradeConstructed
                await self._publish_step(
                    "construction", "constructed",
                    f"SL from {result.construction_source}",
                )
                await self.event_bus.publish(TradeConstructed(
                    symbol=self.symbol,
                    direction=result.signal.direction,
                    sl_source=result.construction_source,
                    sl_distance_pts=0.0,  # TODO: carry from ConstructionResult
                    rr_ratio=result.signal.rr_ratio,
                    candidates_considered=getattr(result.signal, "construction_candidates", 0),
                ))

            _sig_ctx = getattr(result.hypothesis, "source_detail", {}) or {}
            await self._publish_step(
                "signal_gen", "generated",
                f"{result.signal.direction} conf:{result.signal.raw_confidence:.2f} setup:{result.signal.setup_type}",
                context=_sig_ctx,
            )
            await self.event_bus.publish(SignalGenerated(
                symbol=self.symbol,
                instance_id=self.instance_id,
                direction=result.signal.direction,
                confidence=result.signal.raw_confidence,
                setup=result.signal.setup_type,
                signal_mode=signal_mode,
            ))
        elif result.outcome == CycleOutcome.NO_SIGNAL:
            if signal_mode == "ai_signal":
                _neutral = (
                    getattr(self.signal_engine, "last_neutral_reason", None)
                    or getattr(self.signal_engine, "last_error", None)
                )
            else:
                _neutral = getattr(self._algo_engine, "last_neutral_reason", None)
            _neutral_ctx = getattr(self._algo_engine, "last_neutral_context", {})
            await self._publish_step("signal_gen", "no_signal", _neutral or "no setup", context=_neutral_ctx)

        if result.invalidation and result.invalidation.severity != "PASS":
            await self._publish_step(
                "invalidation",
                result.invalidation.severity,
                f"{[f.reason for f in result.invalidation.failures]} penalty={result.invalidation.conviction_penalty}",
            )

        if result.conviction:
            await self._publish_step(
                "conviction",
                result.conviction.decision,
                f"score={result.conviction.score:.1f} | "
                f"penalties={result.conviction.total_penalty:.1f}/{result.conviction.penalty_budget_cap} | "
                f"size={result.conviction.size_scalar}",
            )

        if result.risk_gate:
            status = "passed" if result.risk_gate.allowed else "blocked"
            await self._publish_step(
                "risk_gate", status,
                result.risk_gate.block_reason or f"size_mod={result.risk_gate.size_modifier:.2f}",
            )

        if result.execution_guard:
            await self._publish_step(
                "execution_guard",
                result.execution_guard.action.lower(),
                result.execution_guard.delay_reason or result.execution_guard.block_reason or "clear",
            )

        # Map outcomes to execution / cycle completion
        if result.outcome == CycleOutcome.TRADE_OPENED and result.signal and result.sizing:
            await self._execute_v4_trade(result, context, t0)
        elif result.outcome == CycleOutcome.DELAYED:
            logger.info(
                "[v4] Signal delayed: %s — will re-evaluate next cycle",
                result.rejection_reason,
            )
            await self._publish_cycle_done("blocked", f"DELAY: {result.rejection_reason}")
        elif result.outcome == CycleOutcome.REJECTED:
            await self.event_bus.publish(PipelineBlocked(
                symbol=self.symbol,
                reason=result.rejection_reason or "v4 rejected",
                blocked_by="pipeline_v4",
            ))
            await self._publish_cycle_done("rejected", result.rejection_reason or "")
        elif result.outcome == CycleOutcome.HELD:
            await self._publish_cycle_done("no_signal", f"HELD: {result.rejection_reason}")
        elif result.outcome == CycleOutcome.NO_CONSTRUCTION:
            await self._publish_cycle_done("no_signal", result.rejection_reason or "no valid structure for trade construction")
        else:
            if signal_mode == "ai_signal":
                _neutral = (
                    getattr(self.signal_engine, "last_neutral_reason", None)
                    or getattr(self.signal_engine, "last_error", None)
                )
            else:
                _neutral = getattr(self._algo_engine, "last_neutral_reason", None)
            await self._publish_cycle_done("no_signal", _neutral or "No signal generated")

    async def _execute_v4_trade(self, result, context, t0: float) -> None:
        """Execute a trade from v4 pipeline result — delegates to ExecutionOrchestrator."""
        signal = result.signal

        # Sync orchestrator with current cycle's strategy and canary state
        self._execution_orch.update_state(
            active_strategy=self._active_strategy,
            runtime_strategy=self._active_strategy_runtime(),
            canary_allocation=self._canary_allocation,
            sizer=self.sizer,
            risk_monitor=self.risk_monitor,
            notifier=self.notifier,
        )

        outcome = await self._execution_orch.execute(result, context)

        if outcome.status == "FILLED":
            await self._publish_step(
                "execution", "filled",
                f"#{outcome.broker_ticket} {signal.direction} {outcome.lots:.2f} lots @ {outcome.fill_price or 0:.2f}",
            )
            await self._publish_cycle_done("trade_opened", "")
            return

        logger.warning("[v4] Order %s: %s", outcome.status, outcome.error_message)
        ev_status = "blocked" if outcome.status == "BLOCKED" else "failed"
        await self._publish_step("execution", ev_status, outcome.error_message or "broker rejected")
        await self._publish_cycle_done(
            "blocked" if outcome.status == "BLOCKED" else "order_failed",
            outcome.error_message or "broker rejected",
        )

    async def _reposition_open_trades(self, context: dict) -> None:
        """Evaluate all open trades for repositioning + trailing SL each cycle."""
        if not self.trade_repo or not self.executor:
            return
        try:
            open_trades = await self.trade_repo.get_open_trades(symbol=self.symbol)
        except Exception:
            return

        from alphaloop.risk.trailing_manager import TrailingConfig
        from datetime import datetime, timezone

        runtime_strategy = self._active_strategy_runtime() or {}
        trail_params = dict(runtime_strategy.get("params") or {})
        # Merge tool toggles so TrailingConfig.from_params can read trailing_stop
        trail_params["tools"] = dict(runtime_strategy.get("tools") or {})
        trail_config = TrailingConfig.from_params(trail_params, self.symbol)

        for trade in open_trades:
            try:
                price_data = await self.executor.get_current_price()
                current_price = 0.0
                if price_data:
                    direction = (getattr(trade, "direction", "") or "").upper()
                    if direction == "BUY":
                        current_price = float(price_data.get("bid", 0) or 0)
                    else:
                        current_price = float(price_data.get("ask", 0) or 0)

                # ── Repositioner (reactive: news / vol / spike) ──────────────
                # Enabled by default; disabled only when tool toggle is explicitly False
                reposition_enabled = trail_params["tools"].get("trade_repositioner", True)
                trade_info = {
                    "order_result": {
                        "direction": trade.direction,
                        "entry_price": trade.entry_price,
                        "sl": trade.stop_loss,
                        "tp1": trade.take_profit_1 or 0,
                    }
                }
                events = self._repositioner.check(
                    trade_info, context, current_price=current_price,
                ) if reposition_enabled else []
                for ev in events:
                    if ev.action == "full_close":
                        if not self.dry_run:
                            await self.executor.close_position(trade.order_ticket)
                        trade.outcome = "CLOSED"
                        logger.info("[reposition] Full close trade %s: %s", trade.order_ticket, ev.reason)
                    elif ev.action == "tighten_sl" and ev.new_sl:
                        if not self.dry_run:
                            await self.executor.modify_sl_tp(
                                trade.order_ticket, sl=ev.new_sl, tp=trade.take_profit_1 or 0
                            )
                        trade.stop_loss = ev.new_sl
                        logger.info(
                            "[reposition]%s Tighten SL trade %s → %.5f: %s",
                            "[DRY-RUN]" if self.dry_run else "",
                            trade.order_ticket, ev.new_sl, ev.reason,
                        )
                    elif ev.action == "partial_close" and getattr(ev, "lots", None):
                        if not self.dry_run:
                            await self.executor.close_position(trade.order_ticket, lots=ev.lots)
                        logger.info(
                            "[reposition]%s Partial close trade %s %.2f lots: %s",
                            "[DRY-RUN]" if self.dry_run else "",
                            trade.order_ticket, ev.lots, ev.reason,
                        )

                    await self.event_bus.publish(TradeRepositioned(
                        symbol=self.symbol,
                        instance_id=self.instance_id,
                        trade_id=trade.order_ticket,
                        trigger=ev.trigger,
                        action=ev.action,
                        reason=ev.reason,
                    ))

                # ── Trailing SL (proactive: ratchet SL toward profit) ────────
                if trail_config.enabled and current_price > 0:
                    h1_indicators = (
                        context.get("timeframes", {}).get("H1", {}).get("indicators", {})
                    )
                    atr = float(h1_indicators.get("atr", 0) or 0)
                    trail_ev = self._trailing_manager.evaluate(
                        trade=trade,
                        current_price=current_price,
                        atr=atr,
                        config=trail_config,
                    )
                    if trail_ev:
                        if not self.dry_run:
                            await self.executor.modify_sl_tp(
                                trade.order_ticket,
                                sl=trail_ev.new_sl,
                                tp=trade.take_profit_1 or 0,
                            )
                        logger.info(
                            "[trail-sl]%s ticket=%s %s → SL %.5f (was %.5f) hw=%.5f",
                            " [DRY-RUN]" if self.dry_run else "",
                            trade.order_ticket, trail_ev.trail_type,
                            trail_ev.new_sl, trail_ev.old_sl, trail_ev.new_high_water,
                        )
                        # Persist state even in dry_run (restart resumes correctly)
                        trade.stop_loss = trail_ev.new_sl
                        trade.trail_high_water = trail_ev.new_high_water
                        trade.trail_sl_applied_at = datetime.now(timezone.utc)
                        await self.event_bus.publish(TradeRepositioned(
                            symbol=self.symbol,
                            instance_id=self.instance_id,
                            trade_id=trade.order_ticket,
                            trigger="trailing_sl",
                            action="trail_sl",
                            reason=trail_ev.reason,
                        ))

            except Exception as e:
                logger.warning("[reposition] Error on trade %s: %s", getattr(trade, "order_ticket", "?"), e)

    async def _publish_cycle_done(self, outcome: str, detail: str = "") -> None:
        await self.event_bus.publish(CycleCompleted(
            symbol=self.symbol,
            instance_id=self.instance_id,
            cycle=self._cycle_count,
            outcome=outcome,
            detail=detail,
        ))

    async def _publish_step(
        self,
        stage: str,
        status: str,
        detail: str = "",
        results: list | None = None,
        context: dict | None = None,
    ) -> None:
        await self.event_bus.publish(PipelineStep(
            symbol=self.symbol,
            instance_id=self.instance_id,
            cycle=self._cycle_count,
            stage=stage,
            status=status,
            detail=detail,
            results=results or [],
            context=context or {},
        ))

    async def _run_guards(
        self,
        signal: TradeSignal,
        validated: ValidatedSignal,
        context: dict,
    ) -> str | None:
        """
        Run stateful guards. Returns block reason or None if all pass.
        """
        # Signal hash dedup
        if self._signal_hash.is_duplicate(self.symbol, signal, context):
            return "Duplicate signal (hash filter)"

        # Confidence variance
        self._conf_variance.record(signal.confidence)
        if self._conf_variance.is_unstable():
            return "Unstable AI confidence (variance filter)"

        # Spread regime
        spread = context.get("current_price", {}).get("spread", 0)
        if spread > 0:
            self._spread_regime.record(spread)
            if self._spread_regime.is_spike(spread):
                return f"Spread spike detected ({spread} pts)"

        # Drawdown pause
        if self._dd_pause.is_paused():
            return "Drawdown pause active (accelerating losses)"

        # Near-position dedup — query ALL open trades for this symbol (cross-agent aware)
        h1_ind = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
        atr = h1_ind.get("atr", 0)
        open_trades = []
        if self.trade_repo:
            try:
                # Pass symbol only (no instance_id filter) so we see trades from ALL agents
                open_trades = await self.trade_repo.get_open_trades(symbol=self.symbol)
                open_trades = [
                    {"symbol": self.symbol, "entry_price": getattr(t, "entry_price", 0) if not isinstance(t, dict) else t.get("entry_price", 0),
                     "instance_id": getattr(t, "instance_id", "") if not isinstance(t, dict) else t.get("instance_id", "")}
                    for t in open_trades
                ]
            except Exception:
                pass

        if atr > 0 and self._near_dedup.is_too_close(
            validated.final_entry, atr, open_trades, self.symbol,
        ):
            return "Too close to existing open position (near dedup)"

        # Portfolio cap
        balance = 0.0
        if self.executor:
            try:
                balance = await self.executor.get_account_balance()
            except Exception:
                balance = 10_000.0  # fallback

        if balance > 0:
            open_risk_trades = [
                {"risk_amount_usd": (t.get("risk_amount_usd", 0) if isinstance(t, dict) else getattr(t, "risk_amount_usd", 0)) or 0}
                for t in (open_trades if isinstance(open_trades, list) else [])
            ]
            if self._portfolio_cap.is_capped(open_risk_trades, balance):
                return "Portfolio risk cap exceeded"

        return None

    async def record_trade_close(
        self,
        pnl_usd: float,
        risk_usd: float = 0,
        trade_id: int | None = None,
        trade_data: dict | None = None,
    ) -> None:
        """Feed trade close result to stateful guards and publish event."""
        self._equity_scaler.record_pnl(pnl_usd)
        self._dd_pause.record_close(pnl_usd)

        # Compute and save P&L attribution for the closed trade
        if trade_id is not None and trade_data is not None and self._session_factory:
            try:
                from alphaloop.research.attribution import TradeAttributor
                attributor = TradeAttributor()
                attribution = attributor.compute_attribution(trade_data)
                if any(v is not None for v in attribution.values()):
                    async with self._session_factory() as _attr_session:
                        from alphaloop.db.repositories.trade_repo import TradeRepository
                        _attr_repo = TradeRepository(_attr_session)
                        await _attr_repo.update_attribution(trade_id, attribution)
                        await _attr_session.commit()
            except Exception as _attr_err:
                logger.debug("[attribution] Failed for trade %s: %s", trade_id, _attr_err)

        # Publish TradeClosed event for MetaLoop subscription
        from alphaloop.core.events import TradeClosed
        await self.event_bus.publish(TradeClosed(
            symbol=self.symbol,
            pnl_usd=pnl_usd,
        ))

    async def _load_canary_allocation(self) -> None:
        """Load canary allocation from settings if a canary is active."""
        if not self.settings_service:
            return
        try:
            raw = await self.settings_service.get(f"canary_{self.symbol}")
            if raw:
                canary = json.loads(raw)
                end_time = datetime.fromisoformat(canary.get("end_time", ""))
                if end_time > datetime.now(timezone.utc):
                    pct = canary.get("allocation_pct", 100.0)
                    self._canary_allocation = pct / 100.0
                    logger.info(
                        "[canary] Active canary for %s: %.0f%% allocation until %s",
                        self.symbol, pct, end_time.isoformat(),
                    )
                else:
                    self._canary_allocation = None
        except Exception:
            self._canary_allocation = None

    async def _build_context(self):
        """Build market context with real price data and indicators."""
        from types import SimpleNamespace
        from alphaloop.utils.time import get_session_info

        class AttrDict(dict):
            """Dict that also supports attribute access."""
            def __getattr__(self, key):
                try:
                    return self[key]
                except KeyError:
                    raise AttributeError(key)
            __setattr__ = dict.__setitem__

        # Refresh canary allocation every cycle
        await self._load_canary_allocation()

        # Fetch current price from executor
        current_price: dict = {}
        if self.executor:
            try:
                price_data = await self.executor.get_current_price()
                if price_data:
                    current_price = price_data
            except Exception:
                pass

        # Phase 4B: Fetch candle data through validated OHLCVFetcher when available,
        # falling back to direct MT5 for backward compatibility
        h1_ind: dict = {}
        m15_ind: dict = {}
        try:
            import MetaTrader5 as mt5
            import numpy as np
            import pandas as pd

            # Ensure MT5 is initialized and symbol is selected
            if not self._fetcher or self._fetcher is True:
                if not mt5.terminal_info():
                    mt5.initialize()
                # Resolve broker symbol (e.g. XAUUSD → XAUUSDm on Exness)
                self._mt5_symbol = self.symbol
                if mt5.symbol_info(self.symbol) is None:
                    for suffix in ["m", "M", ".raw", ""]:
                        candidate = self.symbol + suffix
                        if mt5.symbol_info(candidate):
                            self._mt5_symbol = candidate
                            break
                mt5.symbol_select(self._mt5_symbol, True)
                logger.info("[context] MT5 symbol resolved: %s → %s", self.symbol, self._mt5_symbol)
                # Initialize validated fetcher (Phase 4B)
                try:
                    from alphaloop.data.fetcher import OHLCVFetcher
                    self._fetcher = OHLCVFetcher(
                        symbol=self.symbol,
                        executor=self.executor,
                    )
                except Exception:
                    self._fetcher = True  # fallback to raw MT5

            # Use validated fetcher if available
            if hasattr(self._fetcher, "get_ohlcv"):
                try:
                    h1_df = await self._fetcher.get_ohlcv(timeframe="H1", bars=210)
                    rates_arr = h1_df.to_records(index=False) if len(h1_df) > 0 else None
                except Exception as fetch_err:
                    logger.warning("[context] OHLCVFetcher H1 failed, falling back to raw MT5: %s", fetch_err)
                    rates_arr = mt5.copy_rates_from_pos(self._mt5_symbol, mt5.TIMEFRAME_H1, 0, 210)
            else:
                rates_arr = mt5.copy_rates_from_pos(self._mt5_symbol, mt5.TIMEFRAME_H1, 0, 210)

            rates = rates_arr
            if rates is not None and len(rates) > 14:
                df = pd.DataFrame(rates)
                highs = df["high"].values
                lows = df["low"].values
                closes = df["close"].values
                tr = np.maximum(highs[1:] - lows[1:],
                                np.maximum(np.abs(highs[1:] - closes[:-1]),
                                           np.abs(lows[1:] - closes[:-1])))
                atr = float(np.mean(tr[-14:]))
                atr_pct = (atr / closes[-1] * 100) if closes[-1] > 0 else 0.0
                def _ema(arr, period):
                    out = np.empty_like(arr, dtype=float)
                    out[0] = arr[0]
                    k = 2.0 / (period + 1)
                    for i in range(1, len(arr)):
                        out[i] = arr[i] * k + out[i - 1] * (1 - k)
                    return out
                ema21 = _ema(closes, 21)
                ema55 = _ema(closes, 55)
                ema200_h1 = _ema(closes, 200) if len(closes) >= 200 else None
                h1_ind = {
                    "atr": round(atr, 4), "atr_pct": round(atr_pct, 4),
                    "ema_fast": round(float(ema21[-1]), 4),
                    "ema_slow": round(float(ema55[-1]), 4),
                    "close": round(float(closes[-1]), 4),
                    "ema200": round(float(ema200_h1[-1]), 4) if ema200_h1 is not None else None,
                }
        except Exception as e:
            logger.warning("[context] H1 indicators failed: %s", e)

        try:
            import MetaTrader5 as mt5
            import numpy as np
            import pandas as pd

            # Read EMA/RSI periods from active strategy params (Optuna-tuned)
            # Falls back to defaults if no strategy loaded yet
            _p = dict(self._active_strategy_runtime().get("params") or {})
            _ema_fast_p  = int(_p.get("ema_fast",   21))
            _ema_slow_p  = int(_p.get("ema_slow",   55))
            _rsi_period  = int(_p.get("rsi_period", 14))
            _atr_period  = int(_p.get("atr_period", 14))

            # Need enough bars: EMA200 warmup + ema_slow + extra
            _bars_needed = max(_ema_slow_p * 3, 250)

            # Phase 4B: Use validated fetcher for M15 when available
            if hasattr(self._fetcher, "get_ohlcv"):
                try:
                    m15_df = await self._fetcher.get_ohlcv(timeframe="M15", bars=_bars_needed)
                    rates = m15_df.to_records(index=False) if len(m15_df) > 0 else None
                except Exception as m15_err:
                    logger.warning("[context] OHLCVFetcher M15 failed, falling back: %s", m15_err)
                    rates = mt5.copy_rates_from_pos(self._mt5_symbol, mt5.TIMEFRAME_M15, 0, _bars_needed)
            else:
                rates = mt5.copy_rates_from_pos(self._mt5_symbol, mt5.TIMEFRAME_M15, 0, _bars_needed)
            if rates is not None and len(rates) > _ema_slow_p + 2:
                df = pd.DataFrame(rates)
                highs_m15 = df["high"].values
                lows_m15 = df["low"].values
                closes = df["close"].values
                opens = df["open"].values

                # RSI — use strategy rsi_period
                deltas = np.diff(closes)
                gains = np.where(deltas > 0, deltas, 0.0)
                losses = np.where(deltas < 0, -deltas, 0.0)
                avg_gain = float(np.mean(gains[-_rsi_period:]))
                avg_loss = float(np.mean(losses[-_rsi_period:])) or 1e-9
                rsi = 100 - (100 / (1 + avg_gain / avg_loss))

                # ATR — use strategy atr_period
                tr_m15 = np.maximum(highs_m15[1:] - lows_m15[1:],
                                    np.maximum(np.abs(highs_m15[1:] - closes[:-1]),
                                               np.abs(lows_m15[1:] - closes[:-1])))
                atr_m15 = float(np.mean(tr_m15[-_atr_period:]))

                # EMA — use Optuna-tuned periods from strategy card
                def _ema_m15(arr, period):
                    out = np.empty_like(arr, dtype=float)
                    out[0] = arr[0]
                    k = 2.0 / (period + 1)
                    for i in range(1, len(arr)):
                        out[i] = arr[i] * k + out[i - 1] * (1 - k)
                    return out
                ema_fast_m15 = _ema_m15(closes, _ema_fast_p)
                ema_slow_m15 = _ema_m15(closes, _ema_slow_p)

                # ── EMA200 (used by ema200_filter) ──────────────────────────
                ema200_arr = _ema_m15(closes, 200) if len(closes) >= 200 else None
                _ema200_val = round(float(ema200_arr[-1]), 4) if ema200_arr is not None else None

                # ── MACD (used by macd_filter) ──────────────────────────────
                _macd_fast   = int(_p.get("macd_fast",   12))
                _macd_slow_m = int(_p.get("macd_slow",   26))
                _macd_sig    = int(_p.get("macd_signal",  9))
                if len(closes) >= _macd_slow_m + _macd_sig:
                    _macd_line = _ema_m15(closes, _macd_fast) - _ema_m15(closes, _macd_slow_m)
                    _sig_line  = _ema_m15(_macd_line, _macd_sig)
                    _macd_hist = round(float(_macd_line[-1] - _sig_line[-1]), 6)
                else:
                    _macd_hist = None

                # ── Bollinger Bands (used by bollinger_filter) ───────────────
                _bb_period = int(_p.get("bb_period",  20))
                _bb_std    = float(_p.get("bb_std_dev", 2.0))
                if len(closes) >= _bb_period:
                    _bb_sl    = closes[-_bb_period:]
                    _bb_mid   = float(np.mean(_bb_sl))
                    _bb_std_v = float(np.std(_bb_sl))
                    _bb_upper = _bb_mid + _bb_std * _bb_std_v
                    _bb_lower = _bb_mid - _bb_std * _bb_std_v
                    _bb_range = _bb_upper - _bb_lower
                    _pct_b    = (closes[-1] - _bb_lower) / _bb_range if _bb_range > 0 else 0.5
                    _bb_pct_b = round(float(_pct_b), 4)
                    _bb_upper_r = round(float(_bb_upper), 4)
                    _bb_lower_r = round(float(_bb_lower), 4)
                else:
                    _bb_pct_b = None
                    _bb_upper_r = None
                    _bb_lower_r = None

                # ── ADX (used by adx_filter) — matches _adx_simple in backtester ──
                _adx_period = int(_p.get("adx_period", 14))
                if len(closes) >= _adx_period * 2:
                    _plus_dm  = np.maximum(np.diff(highs_m15), 0.0)
                    _minus_dm = np.maximum(-np.diff(lows_m15), 0.0)
                    _dm_mask  = _plus_dm < _minus_dm
                    _plus_dm[_dm_mask]   = 0.0
                    _minus_dm[~_dm_mask] = 0.0
                    _tr_adx   = np.maximum(highs_m15[1:] - lows_m15[1:],
                                           np.abs(highs_m15[1:] - closes[:-1]))
                    _smtr     = np.convolve(_tr_adx,   np.ones(_adx_period) / _adx_period, mode="valid")
                    _smplus   = np.convolve(_plus_dm,  np.ones(_adx_period) / _adx_period, mode="valid")
                    _smminus  = np.convolve(_minus_dm, np.ones(_adx_period) / _adx_period, mode="valid")
                    if len(_smtr) > 0 and _smtr[-1] != 0:
                        _pdi  = 100 * _smplus[-1]  / _smtr[-1]
                        _mdi  = 100 * _smminus[-1] / _smtr[-1]
                        _dnom = _pdi + _mdi
                        _adx_val = round(float(100 * abs(_pdi - _mdi) / _dnom) if _dnom != 0 else 0.0, 2)
                    else:
                        _adx_val = None
                else:
                    _adx_val = None

                # ── VWAP — session proxy using typical price rolling mean ────
                # MT5 synthetic instruments often lack tick volume; use
                # typical_price SMA over last 50 bars as VWAP proxy.
                _vwap_bars = min(50, len(closes))
                if _vwap_bars >= 1:
                    _tp = (highs_m15[-_vwap_bars:] + lows_m15[-_vwap_bars:] + closes[-_vwap_bars:]) / 3.0
                    # Use volume if available (real brokers), else weight all bars equally
                    if "tick_volume" in df.columns:
                        _vols = df["tick_volume"].values[-_vwap_bars:]
                        _vol_sum = float(np.sum(_vols))
                        _vwap_val = round(float(np.sum(_tp * _vols) / _vol_sum), 4) if _vol_sum > 0 else round(float(np.mean(_tp)), 4)
                    else:
                        _vwap_val = round(float(np.mean(_tp)), 4)
                else:
                    _vwap_val = None

                # ── BOS — break of structure ─────────────────────────────────
                _bos_lookback = 20
                if len(closes) >= _bos_lookback + 1:
                    _recent_high  = float(np.max(highs_m15[-_bos_lookback - 1:-1]))
                    _recent_low   = float(np.min(lows_m15[-_bos_lookback - 1:-1]))
                    _bos_bullish  = bool(closes[-1] > _recent_high)
                    _bos_bearish  = bool(closes[-1] < _recent_low)
                    _bos_data = {
                        "bullish_bos": _bos_bullish,
                        "bearish_bos": _bos_bearish,
                        "swing_high":  round(_recent_high, 4),
                        "swing_low":   round(_recent_low, 4),
                        "bullish_break_atr": round((closes[-1] - _recent_high) / atr_m15, 3) if atr_m15 > 0 else 0.0,
                        "bearish_break_atr": round((_recent_low - closes[-1]) / atr_m15, 3) if atr_m15 > 0 else 0.0,
                    }
                else:
                    _bos_data = None

                # ── FVG — fair value gap (3-candle imbalance) ────────────────
                _fvg_scan = min(20, len(closes) - 1)
                _fvg_bull: list[dict] = []
                _fvg_bear: list[dict] = []
                for _fi in range(2, _fvg_scan + 1):
                    _idx = len(closes) - _fvg_scan - 1 + _fi
                    if _idx < 2:
                        continue
                    # Bullish FVG: low[i] > high[i-2]
                    if lows_m15[_idx] > highs_m15[_idx - 2]:
                        _gap_bot = float(highs_m15[_idx - 2])
                        _gap_top = float(lows_m15[_idx])
                        _gap_sz  = (_gap_top - _gap_bot) / atr_m15 if atr_m15 > 0 else 0.0
                        _fvg_bull.append({
                            "bottom":    round(_gap_bot, 4),
                            "top":       round(_gap_top, 4),
                            "midpoint":  round((_gap_bot + _gap_top) / 2, 4),
                            "size_atr":  round(_gap_sz, 3),
                        })
                    # Bearish FVG: high[i] < low[i-2]
                    if highs_m15[_idx] < lows_m15[_idx - 2]:
                        _gap_bot = float(highs_m15[_idx])
                        _gap_top = float(lows_m15[_idx - 2])
                        _gap_sz  = (_gap_top - _gap_bot) / atr_m15 if atr_m15 > 0 else 0.0
                        _fvg_bear.append({
                            "bottom":    round(_gap_bot, 4),
                            "top":       round(_gap_top, 4),
                            "midpoint":  round((_gap_bot + _gap_top) / 2, 4),
                            "size_atr":  round(_gap_sz, 3),
                        })
                _fvg_data = {"bullish": _fvg_bull, "bearish": _fvg_bear}

                # ── Volume ratio (current bar vs 20-bar mean) ────────────────
                _vol_ratio: float | None = None
                if "tick_volume" in df.columns:
                    _vols_all = df["tick_volume"].values
                    if len(_vols_all) >= 21:
                        _mean_vol = float(np.mean(_vols_all[-21:-1]))
                        if _mean_vol > 0:
                            _vol_ratio = round(float(_vols_all[-1]) / _mean_vol, 3)

                # ── Tick jump — 2-bar price move relative to ATR ─────────────
                _tick_jump_atr = (
                    round(abs(float(closes[-1]) - float(closes[-3])) / atr_m15, 3)
                    if atr_m15 > 0 and len(closes) >= 3 else 0.0
                )

                # ── Liquidity vacuum — thin-body candle detection ─────────────
                _bar_range_v = float(highs_m15[-1] - lows_m15[-1])
                _body_v      = abs(float(closes[-1]) - float(opens[-1]))
                _liq_vacuum  = {
                    "bar_range_atr": round(_bar_range_v / atr_m15, 3) if atr_m15 > 0 else 0.0,
                    "body_pct":      round((_body_v / _bar_range_v) * 100, 1) if _bar_range_v > 0 else 100.0,
                }

                # ── Swing structure — HH+HL=bullish, LH+LL=bearish, else ranging ──
                _ss_lookback = 5
                _ss_n = len(highs_m15)
                if _ss_n >= _ss_lookback * 4:
                    _ss_highs: list[float] = []
                    _ss_lows:  list[float] = []
                    for _si in range(_ss_lookback, _ss_n - _ss_lookback):
                        _wh = highs_m15[_si - _ss_lookback:_si + _ss_lookback + 1]
                        _wl = lows_m15[_si - _ss_lookback:_si + _ss_lookback + 1]
                        if highs_m15[_si] == _wh.max():
                            _ss_highs.append(float(highs_m15[_si]))
                        if lows_m15[_si] == _wl.min():
                            _ss_lows.append(float(lows_m15[_si]))
                    if len(_ss_highs) >= 2 and len(_ss_lows) >= 2:
                        _hh = _ss_highs[-1] > _ss_highs[-2]
                        _hl = _ss_lows[-1]  > _ss_lows[-2]
                        _lh = _ss_highs[-1] < _ss_highs[-2]
                        _ll = _ss_lows[-1]  < _ss_lows[-2]
                        if _hh and _hl:
                            _swing_struct = "bullish"
                        elif _lh and _ll:
                            _swing_struct = "bearish"
                        else:
                            _swing_struct = "ranging"
                    else:
                        _swing_struct = "ranging"
                else:
                    _swing_struct = "ranging"

                m15_ind = {
                    "rsi": round(rsi, 2),
                    "close": round(float(closes[-1]), 4),
                    "atr": round(atr_m15, 4),
                    # Param-driven keys (used by AlgorithmicSignalEngine + AI prompt)
                    "ema_fast": round(float(ema_fast_m15[-1]), 4),
                    "ema_slow": round(float(ema_slow_m15[-1]), 4),
                    # Expose actual periods so AI prompt/logs are accurate
                    "ema_fast_period": _ema_fast_p,
                    "ema_slow_period": _ema_slow_p,
                    # Extended indicators for live filter plugins
                    "ema200":         _ema200_val,
                    "macd_histogram": _macd_hist,
                    "bb_pct_b":       _bb_pct_b,
                    "bb_upper":       _bb_upper_r,
                    "bb_lower":       _bb_lower_r,
                    "adx":            _adx_val,
                    "plus_di":        round(float(_pdi), 2) if _adx_val is not None else None,
                    "minus_di":       round(float(_mdi), 2) if _adx_val is not None else None,
                    "vwap":           _vwap_val,
                    "bos":            _bos_data,
                    "fvg":            _fvg_data,
                    "volume_ratio":   _vol_ratio,
                    "tick_jump_atr":  _tick_jump_atr,
                    "liq_vacuum":     _liq_vacuum,
                    "swing_structure": _swing_struct,
                }
        except Exception as e:
            logger.warning("[context] M15 indicators failed: %s", e)

        session_info = get_session_info()
        if isinstance(session_info, dict):
            session_info = SimpleNamespace(**session_info)

        # Fetch external data in parallel (all have built-in caching)
        news_events: list[dict] = []
        dxy_data: dict = {}
        sentiment_data: dict = {}
        try:
            from alphaloop.data.news import fetch_upcoming_news
            from alphaloop.data.dxy import fetch_dxy_bias
            from alphaloop.data.polymarket import fetch_sentiment

            _news_provider = "forexfactory"
            _finnhub_key = None
            _fmp_key = None
            if self.settings_service:
                _news_provider = await self.settings_service.get("NEWS_PROVIDER", "forexfactory")
                from alphaloop.utils.crypto import decrypt_value

                def _decrypt(raw: str) -> str:
                    try:
                        return decrypt_value(raw)
                    except Exception:
                        return raw

                _raw = await self.settings_service.get("FINNHUB_API_KEY", "")
                if _raw:
                    _finnhub_key = _decrypt(_raw)
                _raw = await self.settings_service.get("FMP_API_KEY", "")
                if _raw:
                    _fmp_key = _decrypt(_raw)

            news_events, dxy_data, sentiment_data = await asyncio.gather(
                fetch_upcoming_news(provider=_news_provider, finnhub_key=_finnhub_key, fmp_key=_fmp_key),
                fetch_dxy_bias(),
                fetch_sentiment(),
                return_exceptions=True,
            )
            # Handle exceptions from gather
            if isinstance(news_events, BaseException):
                logger.warning("[context] News fetch failed: %s", news_events)
                news_events = []
            if isinstance(dxy_data, BaseException):
                logger.warning("[context] DXY fetch failed: %s", dxy_data)
                dxy_data = {}
            if isinstance(sentiment_data, BaseException):
                logger.warning("[context] Sentiment fetch failed: %s", sentiment_data)
                sentiment_data = {}
        except Exception as e:
            logger.warning("[context] External data fetch failed: %s", e)

        logger.debug(
            "[context] built: bid=%s ask=%s | M15: ema_fast=%s ema_slow=%s "
            "rsi=%s atr=%s | H1: atr_pct=%s | session=%s(%.2f)",
            current_price.get("bid"), current_price.get("ask"),
            m15_ind.get("ema_fast"), m15_ind.get("ema_slow"),
            m15_ind.get("rsi"), m15_ind.get("atr"),
            h1_ind.get("atr_pct"),
            getattr(session_info, "name", "?"), getattr(session_info, "score", 0.0),
        )
        return AttrDict(
            symbol=self.symbol,
            session=session_info,
            current_price=current_price,
            price=SimpleNamespace(**current_price) if current_price else SimpleNamespace(ask=0, bid=0),
            indicators={"H1": h1_ind, "M15": m15_ind},
            timeframes={"H1": {"indicators": h1_ind}, "M15": {"indicators": m15_ind}},
            upcoming_news=news_events,
            news=news_events,
            dxy=dxy_data,
            macro_sentiment=sentiment_data,
            sentiment=sentiment_data,
            trade_direction="",
            risk_monitor=self.risk_monitor,
        )
