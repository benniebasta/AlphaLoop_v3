"""AppSetting — key-value store for all WebUI-controlled settings."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from alphaloop.db.models.base import Base


class AppSetting(Base):
    """
    Key-value store for application settings set via the WebUI.
    Replaces .env editing — users configure everything through Settings tab.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
