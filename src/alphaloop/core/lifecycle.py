"""Startup and shutdown hooks for the application."""

import logging
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from alphaloop.core.container import Container

logger = logging.getLogger(__name__)


# Column migrations for existing tables.
# Each entry: (table, column, SQL type)
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("trade_logs", "order_ticket", "INTEGER"),
]


async def migrate_missing_columns(engine: AsyncEngine) -> None:
    """Add columns defined in the ORM but missing from the live DB.

    Safe for SQLite and PostgreSQL. Only runs ALTER TABLE for columns
    that are actually absent, so it is idempotent.
    """
    async with engine.begin() as conn:
        for table, column, sql_type in _COLUMN_MIGRATIONS:
            # Check if column already exists
            if "sqlite" in str(engine.url):
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {row[1] for row in result}
            else:
                result = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table}'"
                ))
                existing = {row[0] for row in result}

            if column not in existing:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                ))
                logger.info("[migrate] Added missing column %s.%s (%s)", table, column, sql_type)
            else:
                logger.debug("[migrate] Column %s.%s already exists", table, column)


async def startup(container: Container) -> None:
    """Initialize all application resources."""
    logger.info("Starting AlphaLoop v3...")

    # Initialize database
    await container.init_db()
    logger.info("Database initialized")

    # ── Phase 6A: Only use create_all() in dev mode ────────────────────────
    import os as _os_lc
    _env = _os_lc.environ.get("ENVIRONMENT", "development").lower()
    _is_dev = _env in ("development", "dev", "test")

    if not _is_dev and container.config.db.url.startswith("sqlite"):
        msg = "[schema] Non-dev environments require PostgreSQL. SQLite is dev/test only."
        logger.critical(msg)
        raise SystemExit(msg)

    if _is_dev and container.config.db.url.startswith("sqlite"):
        from alphaloop.db.models import Base
        async with container.db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created (dev-mode create_all)")
    elif not _is_dev:
        logger.info(
            "[schema] Non-dev environment — skipping create_all(). "
            "Ensure Alembic migrations are applied."
        )
    # ─────────────────────────────────────────────────────────────────────────

    # ── Phase 6A+: Add missing columns to existing tables ─────────────────
    if _is_dev:
        await migrate_missing_columns(container.db_engine)

    # ── Phase 6B: Validate ORM schema against live DB ────────────────────
    try:
        from sqlalchemy import text
        async with container.db_engine.connect() as conn:
            if not _is_dev:
                version_row = await conn.execute(text("SELECT version_num FROM alembic_version"))
                live_head = version_row.scalar_one_or_none()
                expected_head = _expected_alembic_head()
                if live_head != expected_head:
                    msg = (
                        f"[schema] CRITICAL: Alembic head mismatch. "
                        f"db={live_head or 'missing'} repo={expected_head}"
                    )
                    logger.critical(msg)
                    raise SystemExit(msg)

            # Get actual DB columns for key tables
            if "sqlite" in container.config.db.url:
                result = await conn.execute(text("PRAGMA table_info(trade_logs)"))
                db_columns = {row[1] for row in result}

                result2 = await conn.execute(text("PRAGMA table_info(order_records)"))
                db_order_cols = {row[1] for row in result2}
            else:
                result = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'trade_logs'"
                ))
                db_columns = {row[0] for row in result}

                result2 = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'order_records'"
                ))
                db_order_cols = {row[0] for row in result2}

            # Check critical columns exist
            _missing = []
            if db_columns and "order_ticket" not in db_columns:
                _missing.append("trade_logs.order_ticket")

            if _missing:
                msg = (
                    f"[schema] CRITICAL: Missing columns in live DB: "
                    f"{', '.join(_missing)}. Run 'alembic upgrade head' to fix."
                )
                logger.critical(msg)
                if not _is_dev:
                    raise SystemExit(msg)
    except SystemExit:
        raise
    except Exception as schema_err:
        if not _is_dev:
            msg = f"[schema] Could not verify schema: {schema_err}"
            logger.critical(msg)
            raise SystemExit(msg)
        logger.warning("[schema] Could not verify schema: %s", schema_err)
    # ─────────────────────────────────────────────────────────────────────────

    # Seed signal defaults into DB (skips keys that already have a value)
    from alphaloop.config.settings_service import SettingsService, SETTING_DEFAULTS
    settings_svc = SettingsService(container.db_session_factory)
    await settings_svc.seed_defaults(SETTING_DEFAULTS)

    # C-05: Assert critical constants match their SETTING_DEFAULTS counterparts.
    # These pairs must stay in sync — a divergence here means trading uses the
    # wrong threshold depending on which value loads first.
    _assert_config_consistency(SETTING_DEFAULTS)

    from alphaloop.core.events import Event
    from alphaloop.data.market_data_service import MarketDataService
    from alphaloop.risk.service import RiskService
    from alphaloop.supervision.service import SupervisionService

    container.market_data_service = MarketDataService(symbol=container.config.broker.symbol)
    container.risk_service = RiskService(container.db_session_factory)
    container.supervision_service = SupervisionService(container.db_session_factory)
    container.event_bus.subscribe(Event, container.supervision_service.record_bus_event)
    await container.supervision_service.record_event(
        category="startup",
        event_type="container_started",
        severity="info",
        message="Application container initialized",
        payload={
            "environment": container.config.environment,
            "db_url": container.config.db.url.split("?")[0],
        },
    )


async def shutdown(container: Container) -> None:
    """Cleanup all application resources."""
    logger.info("Shutting down AlphaLoop v3...")
    if getattr(container, "supervision_service", None):
        await container.supervision_service.record_event(
            category="shutdown",
            event_type="container_stopped",
            severity="info",
            message="Application container shutdown",
        )
    await container.close()
    logger.info("Shutdown complete")


def _assert_config_consistency(setting_defaults: dict[str, str]) -> None:
    """C-05: Verify that constants.py values match their SETTING_DEFAULTS peers.

    Raises SystemExit if a mismatch is detected so the operator is forced to
    reconcile rather than running with silently wrong thresholds.
    """
    from alphaloop.core.constants import (
        CIRCUIT_KILL_COUNT_DEFAULT,
        MIN_CONFIDENCE_DEFAULT,
    )

    checks = [
        ("CIRCUIT_KILL_COUNT", str(CIRCUIT_KILL_COUNT_DEFAULT)),
        ("MIN_CONFIDENCE", str(MIN_CONFIDENCE_DEFAULT)),
    ]
    mismatches = []
    for key, expected in checks:
        db_default = setting_defaults.get(key, "")
        if db_default != expected:
            mismatches.append(
                f"{key}: constants={expected!r} vs SETTING_DEFAULTS={db_default!r}"
            )

    if mismatches:
        msg = (
            "[C-05] Config consistency check FAILED — diverged constants:\n  "
            + "\n  ".join(mismatches)
            + "\nFix: update constants.py or SETTING_DEFAULTS to use the same value."
        )
        logger.critical(msg)
        raise SystemExit(msg)

    logger.debug("[C-05] Config consistency OK (%d checked)", len(checks))


def _expected_alembic_head() -> str:
    versions_dir = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
    revision_pattern = re.compile(r'^\s*revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
    revisions: list[str] = []
    for path in versions_dir.glob("*.py"):
        match = revision_pattern.search(path.read_text(encoding="utf-8"))
        if match:
            revisions.append(match.group(1))
    if not revisions:
        raise RuntimeError("No Alembic revisions found")
    return sorted(revisions)[-1]
