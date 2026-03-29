"""Running instance collision guard model."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class RunningInstance(Base):
    """
    Multi-instance collision guard.
    One row per running bot. Checked at startup to prevent two bots
    trading the same symbol simultaneously.
    """

    __tablename__ = "running_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    strategy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
