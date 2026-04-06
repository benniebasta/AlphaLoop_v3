"""Shared execution service for all live order paths."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from alphaloop.core.setup_types import normalize_pipeline_setup_type
from alphaloop.execution.schemas import OrderResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutionReport:
    order_id: str = ""
    client_order_id: str = ""
    broker_ticket: int | None = None
    status: str = "FAILED"
    requested_price: float | None = None
    fill_price: float | None = None
    fill_volume: float | None = None
    slippage: float | None = None
    error_message: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trade_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.astimezone(timezone.utc).isoformat()
        data["updated_at"] = self.updated_at.astimezone(timezone.utc).isoformat()
        return data


class ExecutionService:
    """Institutional shared execution path for all market orders."""

    def __init__(
        self,
        *,
        session_factory,
        executor,
        control_plane,
        supervision_service=None,
        dry_run: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._executor = executor
        self._control_plane = control_plane
        self._supervision = supervision_service
        self._dry_run = dry_run

    async def execute_market_order(
        self,
        *,
        symbol: str,
        instance_id: str,
        account_balance: float,
        signal,
        sizing: dict,
        stop_loss: float,
        take_profit: float,
        take_profit_2: float | None = None,
        comment: str = "",
        strategy_id: str = "",
        strategy_version: str | None = None,
        signal_payload: dict | None = None,
        validation_payload: dict | None = None,
        market_context_snapshot: dict | None = None,
        session_name: str = "",
        is_dry_run: bool = True,
    ) -> ExecutionReport:
        now = datetime.now(timezone.utc)
        approval = await self._control_plane.preflight(
            symbol=symbol,
            instance_id=instance_id,
            signal=signal,
            sizing=sizing,
            account_balance=account_balance,
            strategy_id=strategy_id,
        )
        if not approval.approved:
            await self._record_block_incident(
                approval.reason,
                incident_type=(
                    "journal_unavailable"
                    if approval.reason == "Order intent journal unavailable"
                    else "pre_trade_block"
                ),
                symbol=symbol,
                instance_id=instance_id,
                payload={"projected_risk_usd": approval.projected_risk_usd},
            )
            return ExecutionReport(
                order_id=approval.order_id,
                client_order_id=approval.client_order_id,
                status="BLOCKED",
                requested_price=self._requested_price(signal),
                error_message=approval.reason,
                created_at=now,
                updated_at=datetime.now(timezone.utc),
            )

        report = ExecutionReport(
            order_id=approval.order_id,
            client_order_id=approval.client_order_id,
            status="APPROVED",
            requested_price=self._requested_price(signal),
            created_at=now,
            updated_at=now,
        )
        try:
            await self._update_order_status(
                approval.order_id,
                "APPROVED",
                requested_price=report.requested_price,
            )

            # Keep the live execution lock held until the broker path resolves.
            pending_trade_id = await self._create_pending_trade_log(
                symbol=symbol,
                instance_id=instance_id,
                signal=signal,
                sizing=sizing,
                stop_loss=stop_loss,
                take_profit=take_profit,
                take_profit_2=take_profit_2,
                strategy_version=strategy_version,
                signal_payload=signal_payload,
                validation_payload=validation_payload,
                market_context_snapshot=market_context_snapshot,
                session_name=session_name,
                is_dry_run=is_dry_run,
                client_order_id=approval.client_order_id,
            )
            if pending_trade_id is None and not is_dry_run:
                error_message = "Pre-broker trade journal unavailable"
                await self._update_order_status(
                    approval.order_id,
                    "FAILED",
                    error_message=error_message,
                )
                await self._record_block_incident(
                    error_message,
                    incident_type="journal_unavailable",
                    symbol=symbol,
                    instance_id=instance_id,
                    payload={"order_id": approval.order_id},
                )
                return ExecutionReport(
                    order_id=approval.order_id,
                    client_order_id=approval.client_order_id,
                    status="FAILED",
                    requested_price=report.requested_price,
                    error_message=error_message,
                    created_at=report.created_at,
                    updated_at=datetime.now(timezone.utc),
                )

            broker_comment = self._with_client_id(comment, approval.client_order_id)
            try:
                result = await self._executor.open_order(
                    direction=getattr(signal, "direction", ""),
                    lots=float(sizing.get("lots", 0.0) or 0.0),
                    sl=stop_loss,
                    tp=take_profit,
                    tp2=take_profit_2,
                    comment=broker_comment,
                )
            except Exception as exc:
                # Broker call failed — mark PENDING trade as FAILED so it doesn't
                # show up as an orphaned position.
                if pending_trade_id is not None:
                    await self._update_trade_outcome(pending_trade_id, "FAILED")
                await self._update_order_status(
                    approval.order_id, "FAILED", error_message=str(exc),
                )
                await self._record_block_incident(
                    str(exc),
                    incident_type="execution_failure",
                    symbol=symbol,
                    instance_id=instance_id,
                    payload={"order_id": approval.order_id},
                )
                return ExecutionReport(
                    order_id=approval.order_id,
                    client_order_id=approval.client_order_id,
                    status="FAILED",
                    requested_price=report.requested_price,
                    error_message=str(exc),
                    created_at=report.created_at,
                    updated_at=datetime.now(timezone.utc),
                )

            return await self._finalize_result(
                result=result,
                approval=approval,
                pending_trade_id=pending_trade_id,
                symbol=symbol,
                instance_id=instance_id,
                signal=signal,
                sizing=sizing,
                stop_loss=stop_loss,
                take_profit=take_profit,
                take_profit_2=take_profit_2,
                strategy_version=strategy_version,
                signal_payload=signal_payload,
                validation_payload=validation_payload,
                market_context_snapshot=market_context_snapshot,
                session_name=session_name,
                is_dry_run=is_dry_run,
            )
        finally:
            if (
                not self._dry_run
                and self._control_plane is not None
                and hasattr(self._control_plane, "release_execution_lock")
            ):
                await self._control_plane.release_execution_lock(
                    symbol, instance_id
                )

    async def _finalize_result(
        self,
        *,
        result: OrderResult,
        approval,
        pending_trade_id: int | None,
        symbol: str,
        instance_id: str,
        signal,
        sizing: dict,
        stop_loss: float,
        take_profit: float,
        take_profit_2: float | None,
        strategy_version: str | None,
        signal_payload: dict | None,
        validation_payload: dict | None,
        market_context_snapshot: dict | None,
        session_name: str,
        is_dry_run: bool,
    ) -> ExecutionReport:
        now = datetime.now(timezone.utc)
        if result.success:
            await self._update_order_status(
                approval.order_id,
                "SENT",
                broker_ticket=result.order_ticket,
                requested_price=self._requested_price(signal),
            )
            # C-01: Promote PENDING → OPEN with broker fill details.
            # If pending_trade_id is set (normal path), update existing row.
            # Otherwise fall back to creating a new record (shouldn't happen).
            if pending_trade_id is not None:
                trade_id = await self._confirm_trade_log(
                    trade_id=pending_trade_id,
                    order_result=result,
                )
            else:
                trade_id = await self._create_trade_log(
                    symbol=symbol,
                    instance_id=instance_id,
                    signal=signal,
                    sizing=sizing,
                    order_result=result,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    take_profit_2=take_profit_2,
                    strategy_version=strategy_version,
                    signal_payload=signal_payload,
                    validation_payload=validation_payload,
                    market_context_snapshot=market_context_snapshot,
                    session_name=session_name,
                    is_dry_run=is_dry_run,
                    client_order_id=approval.client_order_id,
                )
            await self._update_order_status(
                approval.order_id,
                "FILLED",
                broker_ticket=result.order_ticket,
                fill_price=result.fill_price,
                fill_volume=result.fill_volume,
                slippage_points=result.slippage_points,
                spread_at_fill=result.spread_at_fill,
            )
            execution_report = ExecutionReport(
                order_id=approval.order_id,
                client_order_id=approval.client_order_id,
                broker_ticket=result.order_ticket,
                status="FILLED",
                requested_price=self._requested_price(signal),
                fill_price=result.fill_price,
                fill_volume=result.fill_volume,
                slippage=result.slippage_points,
                error_message="",
                created_at=result.executed_at,
                updated_at=now,
                trade_id=trade_id,
            )
            if self._supervision:
                await self._supervision.record_event(
                    category="execution_report",
                    event_type="market_order_filled",
                    severity="info",
                    symbol=symbol,
                    instance_id=instance_id,
                    entity_id=approval.order_id,
                    message=f"Filled ticket={result.order_ticket}",
                    payload=execution_report.to_dict(),
                )
            return execution_report

        status = "REJECTED" if result.error_message else "FAILED"
        # C-01: Mark the PENDING trade as FAILED so reconciler ignores it.
        if pending_trade_id is not None:
            await self._update_trade_outcome(pending_trade_id, status)
        await self._update_order_status(
            approval.order_id,
            status,
            error_message=result.error_message or "broker rejected",
        )
        if self._supervision:
            await self._supervision.record_event(
                category="execution_report",
                event_type="market_order_failed",
                severity="warning",
                symbol=symbol,
                instance_id=instance_id,
                entity_id=approval.order_id,
                message=result.error_message or "broker rejected",
                payload={
                    "order_id": approval.order_id,
                    "client_order_id": approval.client_order_id,
                    "status": status,
                    "error_message": result.error_message,
                },
            )
        return ExecutionReport(
            order_id=approval.order_id,
            client_order_id=approval.client_order_id,
            broker_ticket=result.order_ticket,
            status=status,
            requested_price=self._requested_price(signal),
            error_message=result.error_message,
            created_at=result.executed_at,
            updated_at=now,
        )

    async def _create_pending_trade_log(
        self,
        *,
        symbol: str,
        instance_id: str,
        signal,
        sizing: dict,
        stop_loss: float,
        take_profit: float,
        take_profit_2: float | None,
        strategy_version: str | None,
        signal_payload: dict | None,
        validation_payload: dict | None,
        market_context_snapshot: dict | None,
        session_name: str,
        is_dry_run: bool,
        client_order_id: str,
    ) -> int | None:
        """Write a PENDING trade_log row BEFORE the broker call (C-01)."""
        try:
            async with self._session_factory() as session:
                from alphaloop.db.repositories.trade_repo import TradeRepository
                repo = TradeRepository(session)
                trade = await repo.create(
                    signal_id=client_order_id,
                    client_order_id=client_order_id,
                    symbol=symbol,
                    direction=getattr(signal, "direction", ""),
                    setup_type=self._setup_type(signal),
                    entry_price=self._requested_price(signal),
                    entry_zone_low=self._entry_zone(signal)[0],
                    entry_zone_high=self._entry_zone(signal)[1],
                    stop_loss=stop_loss,
                    take_profit_1=take_profit,
                    take_profit_2=take_profit_2,
                    lot_size=float(sizing.get("lots", 0.0) or 0.0),
                    risk_pct=float(sizing.get("risk_pct", 0.0) or 0.0),
                    risk_amount_usd=float(
                        sizing.get("risk_amount_usd", sizing.get("risk_usd", 0.0)) or 0.0
                    ),
                    outcome="PENDING",
                    instance_id=instance_id,
                    strategy_version=strategy_version,
                    session_name=session_name,
                    is_dry_run=is_dry_run,
                    signal_json=signal_payload,
                    validation_json=validation_payload,
                    market_context_snapshot=market_context_snapshot,
                    margin_required=float(sizing.get("margin_required", 0.0) or 0.0),
                    rr_ratio=float(getattr(signal, "rr_ratio", 0.0) or 0.0),
                )
                await session.commit()
                return int(trade.id)
        except Exception as exc:
            logger.warning("[execution] Failed to write PENDING trade_log: %s", exc)
            return None

    async def _confirm_trade_log(
        self,
        *,
        trade_id: int,
        order_result: OrderResult,
    ) -> int:
        """Promote a PENDING trade_log to OPEN with broker fill details (C-01)."""
        async with self._session_factory() as session:
            from alphaloop.db.repositories.trade_repo import TradeRepository
            repo = TradeRepository(session)
            await repo.update_trade(
                trade_id,
                outcome="OPEN",
                order_ticket=order_result.order_ticket,
                execution_price=order_result.fill_price,
                entry_price=order_result.fill_price or 0.0,
                execution_spread=order_result.spread_at_fill,
                slippage_points=order_result.slippage_points,
                changed_by="execution_service",
            )
            await session.commit()
        return trade_id

    async def _update_trade_outcome(self, trade_id: int, outcome: str) -> None:
        """Update trade_log outcome (e.g. PENDING → FAILED on broker reject)."""
        try:
            async with self._session_factory() as session:
                from alphaloop.db.repositories.trade_repo import TradeRepository
                repo = TradeRepository(session)
                await repo.update_trade(
                    trade_id,
                    outcome=outcome,
                    changed_by="execution_service",
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "[execution] Failed to update trade %d outcome to %s: %s",
                trade_id, outcome, exc,
            )

    async def _update_order_status(self, order_id: str, status: str, **fields: Any) -> None:
        async with self._session_factory() as session:
            from alphaloop.db.repositories.order_repo import OrderRepository

            repo = OrderRepository(session)
            await repo.update_status(order_id, status, **fields)
            await session.commit()

    async def _create_trade_log(
        self,
        *,
        symbol: str,
        instance_id: str,
        signal,
        sizing: dict,
        order_result: OrderResult,
        stop_loss: float,
        take_profit: float,
        take_profit_2: float | None,
        strategy_version: str | None,
        signal_payload: dict | None,
        validation_payload: dict | None,
        market_context_snapshot: dict | None,
        session_name: str,
        is_dry_run: bool,
        client_order_id: str,
    ) -> int:
        async with self._session_factory() as session:
            from alphaloop.db.repositories.trade_repo import TradeRepository

            repo = TradeRepository(session)
            trade = await repo.create(
                signal_id=client_order_id,
                client_order_id=client_order_id,
                symbol=symbol,
                direction=getattr(signal, "direction", ""),
                setup_type=self._setup_type(signal),
                entry_price=order_result.fill_price or self._requested_price(signal),
                entry_zone_low=self._entry_zone(signal)[0],
                entry_zone_high=self._entry_zone(signal)[1],
                stop_loss=stop_loss,
                take_profit_1=take_profit,
                take_profit_2=take_profit_2,
                lot_size=float(sizing.get("lots", 0.0) or 0.0),
                risk_pct=float(sizing.get("risk_pct", 0.0) or 0.0),
                risk_amount_usd=float(
                    sizing.get("risk_amount_usd", sizing.get("risk_usd", 0.0)) or 0.0
                ),
                outcome="OPEN",
                order_ticket=order_result.order_ticket,
                instance_id=instance_id,
                strategy_version=strategy_version,
                session_name=session_name,
                is_dry_run=is_dry_run,
                execution_price=order_result.fill_price,
                execution_spread=order_result.spread_at_fill,
                slippage_points=order_result.slippage_points,
                signal_json=signal_payload,
                validation_json=validation_payload,
                market_context_snapshot=market_context_snapshot,
                margin_required=float(sizing.get("margin_required", 0.0) or 0.0),
                rr_ratio=float(getattr(signal, "rr_ratio", 0.0) or 0.0),
            )
            await session.commit()
            return int(trade.id)

    async def _record_block_incident(
        self,
        details: str,
        *,
        incident_type: str = "journal_unavailable",
        symbol: str,
        instance_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if not self._supervision:
            return
        severity = "critical" if incident_type in {
            "journal_unavailable",
            "execution_failure",
        } else "warning"
        await self._supervision.record_incident(
            incident_type=incident_type,
            details=details,
            severity=severity,
            symbol=symbol,
            instance_id=instance_id,
            source="execution_service",
            payload=payload or {},
        )

    @staticmethod
    def _with_client_id(comment: str, client_order_id: str) -> str:
        cid = client_order_id[:8]
        if comment:
            return f"{comment}|cid:{cid}"[:31]
        return f"cid:{cid}"[:31]

    @staticmethod
    def _entry_zone(signal) -> tuple[float | None, float | None]:
        zone = getattr(signal, "entry_zone", None)
        if not zone:
            return None, None
        try:
            return float(zone[0]), float(zone[1])
        except (TypeError, ValueError, IndexError):
            return None, None

    @staticmethod
    def _setup_type(signal) -> str:
        return normalize_pipeline_setup_type(
            getattr(signal, "setup_type", None)
            or getattr(signal, "setup_tag", None)
            or getattr(signal, "setup", None)
        )

    @classmethod
    def _requested_price(cls, signal) -> float | None:
        low, high = cls._entry_zone(signal)
        if low is None or high is None:
            return None
        return round((low + high) / 2.0, 5)
