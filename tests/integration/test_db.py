"""Tests for database engine, session, and AppSetting model."""

import pytest
from sqlalchemy import select

from alphaloop.db.models.settings import AppSetting


@pytest.mark.asyncio
async def test_create_and_read_setting(db_session):
    setting = AppSetting(key="TEST_KEY", value="test_value")
    db_session.add(setting)
    await db_session.commit()

    result = await db_session.execute(
        select(AppSetting).where(AppSetting.key == "TEST_KEY")
    )
    row = result.scalar_one()
    assert row.value == "test_value"
    assert row.updated_at is not None


@pytest.mark.asyncio
async def test_update_setting(db_session):
    setting = AppSetting(key="UPDATE_KEY", value="old")
    db_session.add(setting)
    await db_session.commit()

    result = await db_session.execute(
        select(AppSetting).where(AppSetting.key == "UPDATE_KEY")
    )
    row = result.scalar_one()
    row.value = "new"
    await db_session.commit()

    result2 = await db_session.execute(
        select(AppSetting).where(AppSetting.key == "UPDATE_KEY")
    )
    row2 = result2.scalar_one()
    assert row2.value == "new"
