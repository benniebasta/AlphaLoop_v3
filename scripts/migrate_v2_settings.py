"""
Migrate settings from v2 (tradingai/alphaloop) database to v3.

Reads all app_settings from the v2 SQLite database and writes them
to the v3 async database via the settings repository.

Usage:
    python scripts/migrate_v2_settings.py [--source PATH] [--dry-run]

The v2 DB is at: C:/Users/benz-/Documents/tradingai/alphaloop/alphaloop.db
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


V2_DB_PATHS = [
    r"C:\Users\benz-\Documents\tradingai\alphaloop\alphaloop.db",
    r"C:\Users\benz-\Documents\alphaloop_v1\alphaloop\alphaloop.db",
]

# Keys to migrate (v2 key -> v3 key mapping, None = same key)
KEY_MAP = {
    # API Keys
    "GEMINI_API_KEY": None,
    "ANTHROPIC_API_KEY": None,
    "OPENAI_API_KEY": None,
    "DEEPSEEK_API_KEY": None,
    "XAI_API_KEY": None,
    "QWEN_API_KEY": None,
    "NEWS_API_KEY": None,

    # Broker / MT5
    "MT5_SERVER": None,
    "MT5_LOGIN": None,
    "MT5_PASSWORD": None,
    "MT5_SYMBOL": None,
    "MT5_TERMINAL_PATH": None,

    # AI Models
    "SIGNAL_PROVIDER": None,
    "SIGNAL_MODEL": None,
    "CLAUDE_MODEL": None,
    "CLAUDE_ENABLED": None,
    "GEMINI_MODEL": None,
    "TRADING_MODE": None,

    # Qwen
    "QWEN_API_BASE": None,
    "QWEN_LOCAL_BASE": None,
    "QWEN_SIGNAL_MODEL": None,
    "QWEN_VALIDATOR_MODEL": None,
    "QWEN_LOCAL_SIGNAL_MODEL": None,
    "QWEN_LOCAL_VALIDATOR_MODEL": None,
    "QWEN_VALIDATOR_TIMEOUT": None,
    "QWEN_VALIDATOR_ENABLED": None,

    # Risk
    "RISK_PCT": None,
    "MAX_DAILY_LOSS_PCT": None,
    "MAX_CONCURRENT_TRADES": None,
    "CONSECUTIVE_LOSS_LIMIT": None,
    "COMMISSION_PER_LOT": None,
    "LEVERAGE": None,
    "CONTRACT_SIZE": None,
    "SL_SLIPPAGE_BUFFER": None,
    "MARGIN_CAP_PCT": None,
    "RISK_SCORE_THRESHOLD": None,
    "MACRO_ABORT_THRESHOLD": None,
    "MAX_PORTFOLIO_HEAT_PCT": None,
    "MAX_SESSION_LOSS_PCT": None,

    # Signal & Validation
    "MIN_CONFIDENCE": None,
    "CLAUDE_MIN_RR": None,
    "MAX_VOLATILITY_ATR_PCT": None,
    "MIN_VOLATILITY_ATR_PCT": None,
    "CLAUDE_CHECK_H1_TREND": None,
    "CLAUDE_CHECK_RSI": None,
    "CLAUDE_RSI_OB": None,
    "CLAUDE_RSI_OS": None,
    "CLAUDE_CHECK_NEWS": None,
    "CLAUDE_CHECK_SETUP": None,
    "CLAUDE_AVOID_SETUPS": None,

    # Guards
    "CHECK_TICK_JUMP": None,
    "TICK_JUMP_ATR_MAX": None,
    "CHECK_LIQ_VACUUM": None,
    "LIQ_VACUUM_SPIKE_MULT": None,
    "LIQ_VACUUM_BODY_PCT": None,
    "CHECK_FVG": None,
    "FVG_MIN_SIZE_ATR": None,
    "FVG_LOOKBACK": None,
    "USE_BOS_GUARD": None,
    "BOS_MIN_BREAK_ATR": None,
    "BOS_SWING_LOOKBACK": None,
    "USE_VWAP_GUARD": None,
    "VWAP_EXTENSION_MAX_ATR": None,
    "USE_CORRELATION_GUARD": None,
    "CORRELATION_THRESHOLD_BLOCK": None,
    "CORRELATION_THRESHOLD_REDUCE": None,
    "TRADE_COOLDOWN_MINUTES": None,
    "MAX_SLIPPAGE_ATR": None,
    "MAX_SIGNAL_AGE_SECONDS": None,

    # Circuit Breaker
    "CIRCUIT_PAUSE_SEC": None,
    "CIRCUIT_KILL_COUNT": None,
    "PIPELINE_SIZE_FLOOR": None,

    # Session
    "SESSION_LONDON_OPEN": None,
    "SESSION_LONDON_CLOSE": None,
    "SESSION_NY_OPEN": None,
    "SESSION_NY_CLOSE": None,
    "NEWS_PRE_MINUTES": None,
    "NEWS_POST_MINUTES": None,
    "MIN_SPREAD_POINTS": None,

    # Telegram
    "TELEGRAM_TOKEN": None,
    "TELEGRAM_CHAT_ID": None,
    "TELEGRAM_ENABLED": None,

    # Evolution / Self-learning
    "EVO_MIN_TRADES": None,
    "EVO_MAX_PARAM_CHANGE": None,
    "EVO_MAX_DRIFT": None,
    "EVO_ROLLBACK_WR_DROP": None,
    "EVO_ROLLBACK_EXP_DROP": None,
    "EVO_DRIFT_BLOCK": None,
    "EVO_OOS_MIN_WR": None,
    "EVO_CONFIDENCE_GATE": None,
    "EVO_PROMOTE_MIN_CYCLES": None,
    "EVO_CANARY_LOT": None,

    # Strategy Params (from optimizer)
    "PARAM_MIN_CONFIDENCE": None,
    "PARAM_MIN_SESSION_SCORE": None,
    "PARAM_RSI_OB": None,
    "PARAM_RSI_OS": None,
    "PARAM_SL_ATR_MULT": None,
    "PARAM_TP1_RR": None,

    # System
    "DRY_RUN": None,
    "LOG_LEVEL": None,
    "ENVIRONMENT": None,
    "DATABASE_URL": None,
    "WEBUI_TOKEN": None,

    # Tool toggles
    "tool_enabled_session_filter": None,
    "tool_enabled_news_filter": None,
    "tool_enabled_volatility_filter": None,
    "tool_enabled_dxy_filter": None,
    "tool_enabled_sentiment_filter": None,
    "tool_enabled_risk_filter": None,
    "tool_enabled_risk_filter_backtest": None,
    "tool_enabled_risk_filter_dry_run": None,
    "tool_enabled_risk_filter_live": None,

    # AI Model Hub
    "AI_MODEL_HUB": None,
}


def read_v2_settings(source_path: str) -> dict[str, str]:
    """Read all settings from v2 SQLite database."""
    conn = sqlite3.connect(source_path)
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    return {k: v for k, v in rows if v is not None}


async def write_v3_settings(settings: dict[str, str], dry_run: bool = False) -> int:
    """Write settings to v3 database."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from alphaloop.db.models.base import Base
    from alphaloop.db.repositories.settings_repo import SettingsRepository

    engine = create_async_engine("sqlite+aiosqlite:///alphaloop.db", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    count = 0
    async with sf() as session:
        repo = SettingsRepository(session)
        for key, value in sorted(settings.items()):
            v3_key = KEY_MAP.get(key)
            if v3_key is None and key in KEY_MAP:
                v3_key = key  # Same key name
            elif v3_key is None:
                continue  # Not in migration map

            if dry_run:
                masked = value
                if any(x in key.upper() for x in ['KEY', 'TOKEN', 'PASSWORD']):
                    masked = value[:4] + '***' if len(value) > 4 else '***'
                print(f"  [DRY] {v3_key:40s} = {masked}")
            else:
                await repo.set(v3_key, value)
                count += 1

        if not dry_run:
            await session.commit()
            print(f"  Committed {count} settings to v3 database")

    await engine.dispose()
    return count


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate v2 settings to v3")
    parser.add_argument("--source", help="Path to v2 database")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    # Find v2 database
    source = args.source
    if not source:
        for p in V2_DB_PATHS:
            if Path(p).exists():
                source = p
                break

    if not source or not Path(source).exists():
        print("ERROR: Could not find v2 database. Use --source to specify path.")
        sys.exit(1)

    print(f"Source: {source}")
    v2_settings = read_v2_settings(source)
    print(f"Found {len(v2_settings)} settings in v2 database\n")

    # Filter to only keys we know about
    migratable = {k: v for k, v in v2_settings.items() if k in KEY_MAP}
    skipped = {k: v for k, v in v2_settings.items() if k not in KEY_MAP}

    print(f"Migrating {len(migratable)} settings:")
    count = await write_v3_settings(migratable, dry_run=args.dry_run)

    if skipped:
        print(f"\nSkipped {len(skipped)} unknown keys:")
        for k in sorted(skipped):
            print(f"  {k}")

    if not args.dry_run:
        print(f"\nMigration complete: {count} settings written to v3")
    else:
        print(f"\nDry run complete. Use without --dry-run to apply.")


if __name__ == "__main__":
    asyncio.run(main())
