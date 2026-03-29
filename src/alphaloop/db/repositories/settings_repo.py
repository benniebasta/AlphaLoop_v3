"""Async repository for AppSetting CRUD operations."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.models.settings import AppSetting


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str, default: str = "") -> str:
        result = await self._session.execute(
            select(AppSetting.value).where(AppSetting.key == key)
        )
        row = result.scalar_one_or_none()
        return row if row is not None else default

    async def get_all(self) -> dict[str, str]:
        result = await self._session.execute(select(AppSetting))
        return {row.key: row.value or "" for row in result.scalars()}

    async def set(self, key: str, value: str) -> None:
        result = await self._session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            self._session.add(AppSetting(key=key, value=value))

    async def set_many(self, settings: dict[str, str]) -> None:
        for key, value in settings.items():
            await self.set(key, value)

    async def delete(self, key: str) -> None:
        await self._session.execute(
            delete(AppSetting).where(AppSetting.key == key)
        )
