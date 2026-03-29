"""GET/PUT /api/settings — read/write app settings from DB."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from alphaloop.db.repositories.settings_repo import SettingsRepository
from alphaloop.webui.deps import get_db_session

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    settings: dict[str, str]


@router.get("")
async def get_settings(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all app settings."""
    repo = SettingsRepository(session)
    settings = await repo.get_all()
    return {"settings": settings}


@router.put("")
async def update_settings(
    body: SettingsUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update multiple settings at once."""
    repo = SettingsRepository(session)
    await repo.set_many(body.settings)
    return {"status": "ok", "updated": list(body.settings.keys())}
