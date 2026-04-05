"""Durable order lifecycle record — Phase 2C.

Tracks order state from PENDING (pre-submit intent) through terminal states.
Phase 5 extends this with client_order_id and RECOVERY_PENDING.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class OrderRecord(Base):
    """Durable record of an order through its lifecycle.

    Non-terminal states: PENDING, SENT, PARTIAL, RECOVERY_PENDING
    Terminal states: FILLED, CANCELLED, REJECTED, FAILED
    """

    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(8))
    lots: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)

    # Broker fill details
    broker_ticket: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_at_fill: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Error info
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Instance tracking
    instance_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Reserved for Phase 5 — nullable, unused until then
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    transitions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
