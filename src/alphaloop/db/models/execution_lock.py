"""Lease-based execution lock — Phase 3D.

Ensures at most one live execution owner per (account, symbol, strategy) scope.
Uses UUID + heartbeat timestamp (not PID alone) for stale-lock detection.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class ExecutionLock(Base):
    """Durable execution lock for single-writer enforcement."""

    __tablename__ = "execution_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_key: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    owner_uuid: Mapped[str] = mapped_column(String(64))
    pid: Mapped[int] = mapped_column(Integer)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    lease_timeout_sec: Mapped[int] = mapped_column(Integer, default=120)
