"""
trading/execution_orchestrator.py — Execution orchestration for the v4 pipeline.

Extracts the sizing → submit → post-fill notification pipeline from
TradingLoop._execute_v4_trade / TradingLoop._submit_execution so it can be
tested independently and reused across execution paths.

Responsibilities:
  1. Build ValidatedSignal adapter from a v4 CandidateSignal (required by sizer)
  2. Compute lot size via PositionSizer
  3. Apply combined conviction scalar and canary allocation scalar
  4. Delegate to ExecutionService for broker submission + DB persistence
  5. On FILLED: register open trade on RiskMonitor, publish TradeOpened event,
     notify via Telegram/notifier, persist guard state
  6. Return ExecutionOutcome so the caller can publish step/cycle events
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from alphaloop.core.events import EventBus, TradeOpened
from alphaloop.core.types import ValidationStatus, SetupType, TrendDirection
from alphaloop.execution.service import ExecutionService
from alphaloop.risk.guard_persistence import save_guard_state
from alphaloop.signals.schema import TradeSignal, ValidatedSignal
from alphaloop.trading.runtime_utils import (
    current_account_balance,
    current_runtime_strategy,
    current_strategy_reference,
    safe_json_payload,
    session_name_from_context,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutionOutcome:
    """Result returned by ExecutionOrchestrator.execute()."""

    status: str  # "FILLED" | "BLOCKED" | "FAILED"
    broker_ticket: int | None = None
    fill_price: float | None = None
    lots: float = 0.0
    error_message: str = ""


class ExecutionOrchestrator:
    """
    Orchestrates sizing, submission, and post-fill notifications for v4 trades.

    Constructed once by TradingLoop and held as ``self._execution_orch``.
    ``update_state`` must be called after every strategy reload so the
    active_strategy reference and canary allocation stay current.
    """

    def __init__(
        self,
        *,
        sizer: Any = None,
        execution_service: ExecutionService,
        event_bus: EventBus,
        symbol: str,
        instance_id: str,
        dry_run: bool = True,
        risk_monitor: Any = None,
        notifier: Any = None,
        settings_service: Any = None,
        guard_state_refs: dict | None = None,
    ) -> None:
        self._sizer = sizer
        self._execution_service = execution_service
        self._event_bus = event_bus
        self.symbol = symbol
        self.instance_id = instance_id
        self._dry_run = dry_run
        self._risk_monitor = risk_monitor
        self._notifier = notifier
        self._settings_service = settings_service
        # References to stateful guard objects for save_guard_state after each fill.
        # These are the same objects owned by TradingLoop — mutations are visible.
        self._guard_refs: dict = guard_state_refs or {}

        # Mutable — updated by TradingLoop after strategy reload / canary load
        self._active_strategy: Any = None
        self._runtime_strategy: dict[str, Any] = {}
        self._canary_allocation: float | None = None

    # ── State updates ────────────────────────────────────────────────────────

    def update_state(
        self,
        *,
        active_strategy: Any,
        runtime_strategy: dict[str, Any] | None = None,
        canary_allocation: float | None,
        sizer: Any = None,
        risk_monitor: Any = None,
        notifier: Any = None,
    ) -> None:
        """Refresh mutable references after a strategy reload or canary change."""
        self._active_strategy = active_strategy
        self._runtime_strategy = dict(runtime_strategy or {})
        self._canary_allocation = canary_allocation
        if sizer is not None:
            self._sizer = sizer
        if risk_monitor is not None:
            self._risk_monitor = risk_monitor
        if notifier is not None:
            self._notifier = notifier

    # ── Public API ───────────────────────────────────────────────────────────

    async def execute(self, result: Any, context: Any) -> ExecutionOutcome:
        """
        Execute a v4 pipeline trade result.

        Parameters
        ----------
        result : PipelineResult
            Output from pipeline orchestrator with ``signal`` and ``sizing``.
        context : MarketContext
            Current cycle context — used for macro_modifier, atr, session.

        Returns
        -------
        ExecutionOutcome
            Caller should publish PipelineStep and CycleCompleted events based
            on the returned status.
        """
        signal = result.signal
        sizing = result.sizing

        # ── Compute combined scalar from all pipeline sizing components ──────
        combined = (
            sizing.conviction_scalar
            * sizing.regime_scalar
            * sizing.freshness_scalar
            * sizing.risk_gate_scalar
            * sizing.equity_curve_scalar
        )

        logger.info(
            "[v4-orch] Executing: %s %s @ [%.2f-%.2f] SL=%.2f TP=%s "
            "conviction=%.2f regime=%.2f fresh=%.2f risk=%.2f ec=%.2f → combined=%.3f",
            signal.direction,
            signal.setup_type,
            signal.entry_zone[0],
            signal.entry_zone[1],
            signal.stop_loss,
            signal.take_profit,
            sizing.conviction_scalar,
            sizing.regime_scalar,
            sizing.freshness_scalar,
            sizing.risk_gate_scalar,
            sizing.equity_curve_scalar,
            combined,
        )

        # ── Build ValidatedSignal adapter for sizer ───────────────────────
        # Sizer expects ValidatedSignal with final_entry / final_sl / risk_score.
        entry_mid = (signal.entry_zone[0] + signal.entry_zone[1]) / 2.0
        tp_list = signal.take_profit if signal.take_profit else [entry_mid]

        _trend = TrendDirection.BULLISH if signal.direction == "BUY" else TrendDirection.BEARISH
        _setup = SetupType.PULLBACK
        try:
            _setup = SetupType(signal.setup_type)
        except (ValueError, KeyError):
            pass

        _trade_signal = TradeSignal(
            trend=_trend,
            setup=_setup,
            entry_zone=list(signal.entry_zone),
            stop_loss=signal.stop_loss,
            take_profit=tp_list,
            confidence=signal.raw_confidence,
            reasoning=signal.reasoning or "v4 pipeline signal",
        )
        _validated = ValidatedSignal(
            original=_trade_signal,
            status=ValidationStatus.APPROVED,
            risk_score=min(0.84, signal.raw_confidence * 0.5 + 0.2),
        )

        # ── Compute lot size ──────────────────────────────────────────────
        if not self._sizer:
            return ExecutionOutcome(
                status="FAILED",
                error_message="Sizer unavailable",
            )

        try:
            lot_size = self._sizer.compute_lot_size(
                _validated,
                macro_modifier=(
                    context.get("macro_modifier", 1.0)
                    if isinstance(context, dict) else 1.0
                ),
                atr_h1=(
                    context.get("atr_h1")
                    if isinstance(context, dict) else None
                ),
                confidence=signal.raw_confidence,
            )
        except (ValueError, Exception) as sz_err:
            logger.warning("[v4-orch] Sizer rejected: %s", sz_err)
            return ExecutionOutcome(status="FAILED", error_message=f"Sizer: {sz_err}")

        if not lot_size:
            return ExecutionOutcome(status="FAILED", error_message="Sizer returned empty lot_size")

        # Apply combined scalar and floor
        lot_size["lots"] = round(lot_size.get("lots", 0) * combined, 2)
        lot_size["lots"] = max(0.01, lot_size["lots"])

        # Apply canary allocation scalar
        if self._canary_allocation is not None and self._canary_allocation < 1.0:
            lot_size["lots"] = max(0.01, lot_size["lots"] * self._canary_allocation)
            logger.info(
                "[v4-orch] Canary allocation %.0f%% applied → %.2f lots",
                self._canary_allocation * 100, lot_size["lots"],
            )

        # ── Submit to broker via ExecutionService ─────────────────────────
        tp_price = tp_list[0] if tp_list else 0.0
        exec_result = await self._submit(
            signal=signal,
            sizing=lot_size,
            stop_loss=signal.stop_loss,
            take_profit=tp_price,
            take_profit_2=tp_list[1] if len(tp_list) > 1 else None,
            comment=f"v4|{signal.setup_type}|{signal.raw_confidence:.2f}",
            validated={
                "stop_loss": signal.stop_loss,
                "take_profit_1": tp_price,
                "take_profit_2": tp_list[1] if len(tp_list) > 1 else None,
                "rr_ratio": getattr(signal, "rr_ratio", None),
            },
            context=context,
        )

        if exec_result.status != "FILLED":
            return ExecutionOutcome(
                status=exec_result.status,
                broker_ticket=exec_result.broker_ticket,
                fill_price=exec_result.fill_price,
                lots=lot_size["lots"],
                error_message=exec_result.error_message,
            )

        # ── Post-fill: register, notify, persist ──────────────────────────
        await self._post_fill(
            signal=signal,
            lot_size=lot_size,
            exec_result=exec_result,
            tp_price=tp_price,
            context=context,
        )

        return ExecutionOutcome(
            status="FILLED",
            broker_ticket=exec_result.broker_ticket,
            fill_price=exec_result.fill_price,
            lots=lot_size["lots"],
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    async def _submit(
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
        """Thin wrapper around ExecutionService.execute_market_order."""
        execution_sizing = dict(sizing)
        if "risk_amount_usd" not in execution_sizing and "risk_usd" in execution_sizing:
            execution_sizing["risk_amount_usd"] = execution_sizing.get("risk_usd")
        runtime_strategy = (
            dict(self._runtime_strategy)
            if self._runtime_strategy
            else current_runtime_strategy(active_strategy=self._active_strategy)
        )
        strategy_ref = current_strategy_reference(
            symbol=self.symbol,
            runtime_strategy=runtime_strategy,
        )

        return await self._execution_service.execute_market_order(
            symbol=self.symbol,
            instance_id=self.instance_id,
            account_balance=current_account_balance(
                risk_monitor=self._risk_monitor,
                sizer=self._sizer,
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
            is_dry_run=self._dry_run,
        )

    async def _post_fill(
        self,
        *,
        signal: Any,
        lot_size: dict,
        exec_result: Any,
        tp_price: float,
        context: Any,
    ) -> None:
        """Register open trade, publish event, notify, save guard state."""
        # Register with risk monitor so _open_trades count stays accurate
        if self._risk_monitor:
            try:
                await self._risk_monitor.register_open(
                    risk_usd=lot_size.get("risk_amount_usd", 0.0)
                )
            except Exception as e:
                logger.warning("[v4-orch] risk_monitor.register_open failed: %s", e)

        # Publish TradeOpened event
        await self._event_bus.publish(TradeOpened(
            symbol=self.symbol,
            direction=signal.direction,
            entry_price=exec_result.fill_price or 0.0,
            lot_size=lot_size["lots"],
            order_ticket=exec_result.broker_ticket,
            stop_loss=signal.stop_loss,
            take_profit=tp_price,
            confidence=signal.raw_confidence,
        ))

        # Telegram / notification alert
        if self._notifier:
            try:
                current_session_name = session_name_from_context(context)
                await self._notifier.alert_trade_opened(
                    direction=signal.direction,
                    symbol=self.symbol,
                    entry=exec_result.fill_price or (
                        (signal.entry_zone[0] + signal.entry_zone[1]) / 2.0
                    ),
                    sl=signal.stop_loss,
                    tp1=tp_price,
                    lots=lot_size["lots"],
                    confidence=signal.raw_confidence,
                    setup=signal.setup_type,
                    session=current_session_name,
                )
            except Exception as e:
                logger.warning("[v4-orch] Notifier alert failed: %s", e)

        # Persist guard state so dedup / variance / drawdown filters survive restarts
        if self._settings_service and self._guard_refs:
            try:
                await save_guard_state(
                    self._settings_service,
                    hash_filter=self._guard_refs.get("hash_filter"),
                    conf_variance=self._guard_refs.get("conf_variance"),
                    spread_regime=self._guard_refs.get("spread_regime"),
                    equity_scaler=self._guard_refs.get("equity_scaler"),
                    dd_pause=self._guard_refs.get("dd_pause"),
                )
            except Exception as e:
                logger.warning("[v4-orch] save_guard_state failed: %s", e)
