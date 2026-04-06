"""
Institutional pre-trade control plane.

Adds a deterministic approval layer ahead of broker submission:
1. Projected portfolio-risk approval using the sized trade risk.
2. Durable order-intent journaling before the broker call.

Institutional desks generally require both. A system that can submit an
order without first writing an immutable intent record is difficult to
reconcile after crashes, retries, or partial failures.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from alphaloop.core.setup_types import normalize_pipeline_setup_type
from alphaloop.db.models.execution_lock import ExecutionLock
from alphaloop.db.repositories.order_repo import OrderRepository
from alphaloop.execution.order_state import compute_client_order_id

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PreTradeApproval:
    approved: bool
    reason: str = ""
    order_id: str = ""
    client_order_id: str = ""
    projected_risk_usd: float = 0.0
    order_intent_persisted: bool = False


class InstitutionalControlPlane:
    """
    Centralises live-capital pre-trade controls.

    The control plane deliberately fails closed in live mode if order-intent
    persistence is unavailable.
    """

    # Lock TTL: hold for at most 30 seconds. Background reconciler cleans stale locks.
    _LOCK_TTL_SEC = 30

    def __init__(
        self,
        *,
        session_factory=None,
        cross_risk=None,
        dry_run: bool = True,
        enforce_live_journal: bool = True,
        instance_id: str = "",
    ) -> None:
        self._session_factory = session_factory
        self._cross_risk = cross_risk
        self._dry_run = dry_run
        self._enforce_live_journal = enforce_live_journal
        self._instance_id = instance_id or uuid.uuid4().hex[:8]
        self._last_lock_error = ""

    async def preflight(
        self,
        *,
        symbol: str,
        instance_id: str,
        signal,
        sizing: dict,
        account_balance: float,
        strategy_id: str = "",
    ) -> PreTradeApproval:
        """
        Approve or block a trade before broker submission.

        Returns a generated order ID even in dry-run mode so downstream
        logging can stay consistent.
        """
        projected_risk = self._extract_projected_risk(sizing)

        # C-06: Acquire cross-instance advisory lock BEFORE portfolio risk check.
        # Serialises concurrent executions on the same symbol across instances.
        # Dry-run skips the lock (no real capital at risk).
        lock_acquired = False
        if not self._dry_run and self._session_factory is not None:
            lock_acquired = await self._acquire_execution_lock(symbol, instance_id)
            if not lock_acquired:
                reason = self._last_lock_error or (
                    f"Execution lock for {symbol} held by another instance — try again next cycle"
                )
                return PreTradeApproval(
                    approved=False,
                    reason=reason,
                    projected_risk_usd=projected_risk,
                )

        if self._cross_risk is not None:
            allowed, reason = await self._cross_risk.can_open_trade(
                account_balance,
                additional_risk_usd=projected_risk,
            )
            if not allowed:
                if lock_acquired:
                    await self._release_execution_lock(symbol, instance_id)
                return PreTradeApproval(
                    approved=False,
                    reason=reason,
                    projected_risk_usd=projected_risk,
                )

        order_id = uuid.uuid4().hex[:16]
        client_order_id = self._build_client_order_id(
            symbol=symbol,
            signal=signal,
            strategy_id=strategy_id,
        )
        persisted = await self._persist_order_intent(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            instance_id=instance_id,
            signal=signal,
            sizing=sizing,
        )

        if not persisted and self._enforce_live_journal and not self._dry_run:
            if lock_acquired:
                await self._release_execution_lock(symbol, instance_id)
            return PreTradeApproval(
                approved=False,
                reason="Order intent journal unavailable",
                order_id=order_id,
                client_order_id=client_order_id,
                projected_risk_usd=projected_risk,
                order_intent_persisted=False,
            )

        return PreTradeApproval(
            approved=True,
            order_id=order_id,
            client_order_id=client_order_id,
            projected_risk_usd=projected_risk,
            order_intent_persisted=persisted,
        )

    async def _acquire_execution_lock(self, symbol: str, instance_id: str) -> bool:
        """
        Try to acquire a cross-instance advisory lock for this symbol (C-06).

        Returns True if the lock was acquired, False if another instance holds it.
        Stale locks (TTL expired) are removed and reacquired.
        """
        self._last_lock_error = ""
        scope_key = f"exec:{symbol}"
        now = datetime.now(timezone.utc)
        expiry_cutoff = now - timedelta(seconds=self._LOCK_TTL_SEC)

        try:
            async with self._session_factory() as session:
                # Remove any expired locks first (stale from crashes)
                await session.execute(
                    delete(ExecutionLock).where(
                        ExecutionLock.scope_key == scope_key,
                        ExecutionLock.heartbeat_at < expiry_cutoff,
                    )
                )
                await session.flush()

                # Try to insert our lock
                lock = ExecutionLock(
                    scope_key=scope_key,
                    owner_uuid=instance_id,
                    pid=os.getpid(),
                    heartbeat_at=now,
                    acquired_at=now,
                    lease_timeout_sec=self._LOCK_TTL_SEC,
                )
                session.add(lock)
                try:
                    await session.commit()
                    logger.debug(
                        "[control-plane] Execution lock acquired for %s by %s",
                        symbol, instance_id,
                    )
                    return True
                except IntegrityError:
                    await session.rollback()
                    logger.info(
                        "[control-plane] Execution lock for %s held by another instance",
                        symbol,
                    )
                    return False
        except Exception as exc:
            self._last_lock_error = (
                f"Execution lock infrastructure unavailable for {symbol}: {exc}"
            )
            logger.error("[control-plane] %s", self._last_lock_error)
            return False

    async def release_execution_lock(self, symbol: str, instance_id: str) -> None:
        """Public release hook for the execution service."""
        await self._release_execution_lock(symbol, instance_id)

    async def _release_execution_lock(self, symbol: str, instance_id: str) -> None:
        """Release the advisory lock for this symbol/instance."""
        scope_key = f"exec:{symbol}"
        try:
            async with self._session_factory() as session:
                await session.execute(
                    delete(ExecutionLock).where(
                        ExecutionLock.scope_key == scope_key,
                        ExecutionLock.owner_uuid == instance_id,
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "[control-plane] Lock release failed (will expire via TTL): %s", exc
            )

    @staticmethod
    def _extract_projected_risk(sizing: dict) -> float:
        for key in ("risk_amount_usd", "risk_usd"):
            value = sizing.get(key)
            if value is not None:
                return float(value)
        return 0.0

    @staticmethod
    def _entry_reference(signal) -> float:
        zone = getattr(signal, "entry_zone", None) or (0.0, 0.0)
        if not zone:
            return 0.0
        try:
            return round((float(zone[0]) + float(zone[1])) / 2.0, 5)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _build_client_order_id(
        *,
        symbol: str,
        signal,
        strategy_id: str = "",
    ) -> str:
        generated_at = getattr(signal, "generated_at", None)
        if generated_at is not None and getattr(generated_at, "tzinfo", None) is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        if generated_at is None:
            timestamp_bucket = "unknown"
        else:
            timestamp_bucket = generated_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M")

        setup_type = normalize_pipeline_setup_type(
            getattr(signal, "setup_type", "") or getattr(signal, "setup_tag", "")
        )
        signal_id = f"{symbol}:{setup_type}:{timestamp_bucket}"
        return compute_client_order_id(
            signal_id=signal_id,
            symbol=symbol,
            direction=getattr(signal, "direction", ""),
            timestamp_bucket=timestamp_bucket,
            strategy_id=strategy_id,
        )

    async def _persist_order_intent(
        self,
        *,
        order_id: str,
        client_order_id: str,
        symbol: str,
        instance_id: str,
        signal,
        sizing: dict,
    ) -> bool:
        if not self._session_factory:
            logger.warning("[control-plane] No session_factory; cannot persist order intent")
            return False

        try:
            async with self._session_factory() as session:
                repo = OrderRepository(session)
                await repo.create(
                    order_id=order_id,
                    symbol=symbol,
                    direction=getattr(signal, "direction", ""),
                    lots=float(sizing.get("lots", 0.0) or 0.0),
                    instance_id=instance_id,
                    client_order_id=client_order_id,
                    requested_price=self._entry_reference(signal),
                )
                await session.commit()
            return True
        except Exception as exc:
            logger.warning("[control-plane] Failed to persist order intent: %s", exc)
            return False
