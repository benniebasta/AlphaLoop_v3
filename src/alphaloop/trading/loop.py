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
    SignalGenerated,
    SignalValidated,
    SignalRejected,
    TradeOpened,
    PipelineBlocked,
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
        validator=None,
        sizer=None,
        executor=None,
        risk_monitor=None,
        filter_pipeline=None,
        trade_repo=None,
        notifier=None,
        ai_caller=None,
        signal_model_id: str = "",
        settings_service=None,
        tool_registry=None,
    ):
        self.symbol = symbol
        self.instance_id = instance_id
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.event_bus = event_bus or EventBus()

        # Injected components
        self.signal_engine = signal_engine
        self.validator = validator
        self.sizer = sizer
        self.executor = executor
        self.risk_monitor = risk_monitor
        self.filter_pipeline = filter_pipeline
        self.trade_repo = trade_repo
        self.notifier = notifier
        self.ai_caller = ai_caller
        self.signal_model_id = signal_model_id
        self.settings_service = settings_service
        self.tool_registry = tool_registry

        self._running = False
        self._circuit = CircuitBreaker()
        self._heartbeat = HeartbeatWriter()
        self._cycle_count = 0

        # Strategy-driven state (loaded from DB each cycle)
        self._active_strategy = None       # ActiveStrategyConfig | None
        self._strategy_pipeline = None     # FilterPipeline | None
        self._overlay_pipeline = None      # FilterPipeline | None (dry run only)
        self._algo_engine = None           # AlgorithmicSignalEngine | None

        # Stateful guards (persist across cycles)
        self._signal_hash = SignalHashFilter(window=3)
        self._conf_variance = ConfidenceVarianceFilter(window=3, max_stdev=0.15)
        self._spread_regime = SpreadRegimeFilter(window=50, threshold=1.8)
        self._equity_scaler = EquityCurveScaler(window=20)
        self._dd_pause = DrawdownPauseGuard(pause_minutes=30)
        self._near_dedup = NearDedupGuard(min_atr_distance=1.0)
        self._portfolio_cap = PortfolioCapGuard(max_portfolio_risk_pct=6.0)

        # Canary state (loaded from DB settings)
        self._canary_allocation: float | None = None  # e.g. 0.10 = 10%

    async def run(self) -> None:
        """Main loop — runs until stopped."""
        self._running = True
        logger.info(
            "Trading loop started | symbol=%s | instance=%s | dry_run=%s",
            self.symbol, self.instance_id, self.dry_run,
        )

        while self._running:
            try:
                await self._cycle()
            except Exception as e:
                logger.error("Trading cycle error: %s", e, exc_info=True)
                self._circuit.record_failure()
                if self._circuit.should_kill and self.risk_monitor:
                    logger.critical("Circuit breaker kill threshold — activating kill switch")
                    self.risk_monitor._kill_switch_active = True
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

    async def _ensure_strategy_loaded(self) -> None:
        """Load active strategy from DB and build pipeline if version changed."""
        if not self.settings_service or not self.tool_registry:
            return

        from alphaloop.trading.strategy_loader import (
            load_active_strategy, build_strategy_pipeline,
        )

        config = await load_active_strategy(self.settings_service, self.symbol)
        if config is None:
            self._active_strategy = None
            self._strategy_pipeline = None
            self._algo_engine = None
            return

        # Only rebuild if version changed
        if self._active_strategy and self._active_strategy.version == config.version:
            return

        self._active_strategy = config
        self._strategy_pipeline = build_strategy_pipeline(config, self.tool_registry)

        # Update signal model from strategy's AI models
        if config.ai_models.get("signal"):
            self.signal_model_id = config.ai_models["signal"]

        # Create algorithmic engine with strategy params
        from alphaloop.signals.algorithmic import AlgorithmicSignalEngine
        prev_state = None
        if self._algo_engine:
            prev_state = {
                "fast": self._algo_engine._prev_fast,
                "slow": self._algo_engine._prev_slow,
            }
        self._algo_engine = AlgorithmicSignalEngine(
            self.symbol, config.params, prev_ema_state=prev_state,
        )

        logger.info(
            "[loop] Loaded strategy %s v%d (mode=%s, tools=%d)",
            config.symbol, config.version, config.signal_mode,
            sum(1 for v in config.tools.values() if v),
        )

    async def _ensure_overlay_loaded(self) -> None:
        """Load dry-run overlay from DB if in dry-run mode."""
        if not self.dry_run or not self._active_strategy:
            self._overlay_pipeline = None
            return

        if not self.settings_service or not self.tool_registry:
            return

        from alphaloop.trading.overlay_loader import (
            load_overlay_config, build_overlay_pipeline,
        )

        config = await load_overlay_config(
            self.settings_service, self.symbol, self._active_strategy.version,
        )
        if config is None:
            self._overlay_pipeline = None
            return

        strategy_tools = set(
            name for name, on in self._active_strategy.tools.items() if on
        )
        self._overlay_pipeline = build_overlay_pipeline(
            config, self.tool_registry, exclude_tools=strategy_tools,
        )

    async def _cycle(self) -> None:
        """Single trading cycle."""
        self._cycle_count += 1
        t0 = time.time()

        # 1. Risk pre-check
        if self.risk_monitor:
            can_trade, reason = await self.risk_monitor.can_open_trade()
            if not can_trade:
                logger.info("[cycle] Blocked by risk monitor: %s", reason)
                return

        # 2. Circuit breaker
        if self._circuit.is_open:
            logger.info("[cycle] Circuit breaker open — skipping")
            return

        # 2b. Load active strategy + overlay from DB (hot-reloads on version change)
        await self._ensure_strategy_loaded()
        await self._ensure_overlay_loaded()

        # 3. Build market context (placeholder — data layer provides this)
        context = await self._build_context()

        # 4a. Run strategy pipeline (from strategy JSON tools)
        active_pipeline = self._strategy_pipeline or self.filter_pipeline
        if active_pipeline:
            pipeline_result = await active_pipeline.run(context)
            if pipeline_result.get("blocked"):
                logger.info(
                    "[cycle] Pipeline blocked: %s",
                    pipeline_result.get("block_reason"),
                )
                await self.event_bus.publish(PipelineBlocked(
                    symbol=self.symbol,
                    reason=pipeline_result.get("block_reason", ""),
                    blocked_by=pipeline_result.get("blocked_by", ""),
                ))
                return

        # 4b. Run overlay pipeline (dry run only, appended after strategy tools)
        if self._overlay_pipeline:
            overlay_result = await self._overlay_pipeline.run(context)
            if overlay_result.get("blocked"):
                logger.info(
                    "[cycle] Overlay blocked: %s",
                    overlay_result.get("block_reason"),
                )
                await self.event_bus.publish(PipelineBlocked(
                    symbol=self.symbol,
                    reason=overlay_result.get("block_reason", ""),
                    blocked_by=f"overlay:{overlay_result.get('blocked_by', '')}",
                ))
                return

        # 5. Generate signal (branch on signal mode)
        signal_mode = (
            self._active_strategy.signal_mode
            if self._active_strategy else "algo_plus_ai"
        )

        if signal_mode == "algo_only" and self._algo_engine:
            # Mode A: deterministic algorithm only
            signal = await self._algo_engine.generate_signal(context)
        elif self._algo_engine:
            # Mode B: algorithm generates, AI validates in step 6
            signal = await self._algo_engine.generate_signal(context)
        elif self.signal_engine:
            # Fallback: AI-only (no active strategy loaded)
            signal = await self.signal_engine.generate_signal(
                context,
                ai_caller=self.ai_caller,
                model_id=self.signal_model_id,
            )
        else:
            return

        if signal is None:
            logger.info("[cycle] No signal generated")
            self._circuit.record_success()
            return

        self._circuit.record_success()

        await self.event_bus.publish(SignalGenerated(
            symbol=self.symbol,
            signal=signal,
        ))

        # 6. Validate signal
        validation_overrides = (
            self._active_strategy.validation if self._active_strategy else None
        )
        if signal_mode == "algo_only":
            # Mode A: hard rules only, skip AI validation
            if self.validator:
                validated = await self.validator.validate(
                    signal, context,
                    validation_overrides=validation_overrides,
                )
            else:
                validated = ValidatedSignal(
                    original=signal,
                    status=ValidationStatus.APPROVED,
                    risk_score=0.3,
                )
        elif self.validator:
            # Mode B: hard rules + AI validation
            validated = await self.validator.validate(
                signal, context, ai_caller=self.ai_caller,
                validation_overrides=validation_overrides,
            )
        else:
            validated = ValidatedSignal(
                original=signal,
                status=ValidationStatus.APPROVED,
                risk_score=0.3,
            )

        if validated.status != ValidationStatus.APPROVED:
            logger.info(
                "[cycle] Signal rejected: %s", validated.rejection_reasons
            )
            await self.event_bus.publish(SignalRejected(
                symbol=self.symbol,
                reason="; ".join(validated.rejection_reasons),
                rejected_by="validator",
            ))
            if self.notifier:
                await self.notifier.alert_signal_rejected(
                    self.symbol, validated.rejection_reasons
                )
            return

        await self.event_bus.publish(SignalValidated(
            symbol=self.symbol,
            signal=validated,
            approved=True,
        ))

        # 6b. Stateful guards (post-validation, pre-sizing)
        guard_block = await self._run_guards(signal, validated, context)
        if guard_block:
            logger.info("[cycle] Blocked by guard: %s", guard_block)
            await self.event_bus.publish(SignalRejected(
                symbol=self.symbol,
                reason=guard_block,
                rejected_by="guard",
            ))
            return

        # 7. Size position
        if not self.sizer:
            logger.warning("[cycle] No sizer configured")
            return

        try:
            sizing = self.sizer.compute_lot_size(validated)
        except ValueError as e:
            logger.warning("[cycle] Sizing rejected: %s", e)
            return

        # Apply guard modifiers
        equity_scale = self._equity_scaler.risk_scale()
        if equity_scale < 1.0:
            sizing["lots"] = max(0.01, sizing["lots"] * equity_scale)
            sizing["risk_amount_usd"] *= equity_scale
            logger.info("[cycle] Equity scaler reduced lots to %.2f", sizing["lots"])

        # Apply canary allocation if active
        if self._canary_allocation is not None and self._canary_allocation < 1.0:
            sizing["lots"] = max(0.01, sizing["lots"] * self._canary_allocation)
            sizing["risk_amount_usd"] *= self._canary_allocation
            logger.info(
                "[cycle] Canary allocation %.0f%% — lots reduced to %.2f",
                self._canary_allocation * 100, sizing["lots"],
            )

        # 8. Execute order
        if not self.executor:
            logger.warning("[cycle] No executor configured")
            return

        result = await self.executor.open_order(
            direction=signal.direction,
            lots=sizing["lots"],
            sl=validated.final_sl,
            tp=validated.final_tp[0] if validated.final_tp else 0,
            comment=f"AL3|{self.instance_id}|{signal.setup.value}",
        )

        if result.success:
            logger.info(
                "[cycle] Order placed: ticket=%s %s %.2f lots",
                result.order_ticket, signal.direction, sizing["lots"],
            )

            if self.risk_monitor:
                await self.risk_monitor.register_open(
                    risk_usd=sizing["risk_amount_usd"]
                )

            await self.event_bus.publish(TradeOpened(
                symbol=self.symbol,
                direction=signal.direction,
                entry_price=result.fill_price or 0.0,
                lot_size=sizing["lots"],
                trade_id=result.order_ticket,
            ))

            if self.notifier:
                await self.notifier.alert_trade_opened(
                    direction=signal.direction,
                    symbol=self.symbol,
                    entry=result.fill_price or validated.final_entry,
                    sl=validated.final_sl,
                    tp1=validated.final_tp[0] if validated.final_tp else 0,
                    lots=sizing["lots"],
                    confidence=signal.confidence,
                    setup=signal.setup.value,
                    session=context.get("session", {}).get("name", ""),
                )

            # Log to DB
            if self.trade_repo:
                await self.trade_repo.create(
                    symbol=self.symbol,
                    direction=signal.direction,
                    outcome="OPEN",
                    instance_id=self.instance_id,
                    entry_price=result.fill_price or validated.final_entry,
                    lot_size=sizing["lots"],
                    stop_loss=validated.final_sl,
                    take_profit_1=validated.final_tp[0] if validated.final_tp else None,
                    risk_amount_usd=sizing["risk_amount_usd"],
                    confidence=signal.confidence,
                    setup_type=signal.setup.value,
                    signal_reasoning=signal.reasoning,
                    risk_score=validated.risk_score,
                    order_ticket=result.order_ticket,
                )
        else:
            logger.error("[cycle] Order failed: %s", result.error_message)

        elapsed = time.time() - t0
        logger.info("[cycle] Completed in %.1fs", elapsed)

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

        # Near-position dedup
        h1_ind = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
        atr = h1_ind.get("atr", 0)
        open_trades = []
        if self.trade_repo:
            try:
                open_trades = await self.trade_repo.get_open(symbol=self.symbol)
                open_trades = [
                    {"symbol": self.symbol, "entry_price": getattr(t, "entry_price", 0) if not isinstance(t, dict) else t.get("entry_price", 0)}
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
                balance = await self.executor.get_balance()
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

    async def record_trade_close(self, pnl_usd: float, risk_usd: float = 0) -> None:
        """Feed trade close result to stateful guards and publish event."""
        self._equity_scaler.record_pnl(pnl_usd)
        self._dd_pause.record_close(pnl_usd)

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

    async def _build_context(self) -> dict:
        """Build market context with real price data when available."""
        from alphaloop.utils.time import get_session_info

        # Refresh canary allocation every cycle
        await self._load_canary_allocation()

        # Fetch current price from executor if available
        current_price: dict = {}
        if self.executor:
            try:
                price_data = await self.executor.get_current_price(self.symbol)
                if price_data:
                    current_price = price_data
            except Exception:
                pass  # fail-open: spread guard will just skip

        return {
            "session": get_session_info(),
            "current_price": current_price,
            "timeframes": {"H1": {"indicators": {}}, "M15": {"indicators": {}}},
            "upcoming_news": [],
            "dxy": {},
            "macro_sentiment": {},
        }
