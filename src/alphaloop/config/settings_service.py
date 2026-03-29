"""
Settings service — merges .env defaults with DB overrides.

Replaces the v2 settings_store.py (raw sqlite3) with async SQLAlchemy access.
Provides typed config access with caching and thread-safe reload.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alphaloop.db.models.settings import AppSetting

logger = logging.getLogger(__name__)

# Cache TTL in seconds
_CACHE_TTL = 60.0

# Default values seeded into the DB on first startup (only if key is absent).
# Covers Signal tab (25 keys) + Tools tab (43 keys) = 68 keys total.
SETTING_DEFAULTS: dict[str, str] = {
    # ── Signal: Core Thresholds ──────────────────────────────────────────────
    "TRADING_MODE":              "swing",
    "MIN_CONFIDENCE":            "0.55",
    "CLAUDE_MIN_RR":             "1.5",
    "MAX_VOLATILITY_ATR_PCT":    "2.5",
    "MIN_VOLATILITY_ATR_PCT":    "0.05",
    "TRADE_COOLDOWN_MINUTES":    "15",
    "MAX_SLIPPAGE_ATR":          "0.5",
    "MAX_SIGNAL_AGE_SECONDS":    "60",
    # ── Signal: Validation Guards ─────────────────────────────────────────────
    "CLAUDE_CHECK_H1_TREND":     "true",
    "CLAUDE_CHECK_RSI":          "true",
    "CLAUDE_RSI_OB":             "70",
    "CLAUDE_RSI_OS":             "30",
    "CLAUDE_CHECK_NEWS":         "true",
    "CLAUDE_CHECK_SETUP":        "true",
    "CLAUDE_AVOID_SETUPS":       "",
    # ── Signal: Entry Parameters ──────────────────────────────────────────────
    "PARAM_SL_ATR_MULT":         "1.5",
    "PARAM_TP1_RR":              "1.5",
    "PARAM_TP2_RR":              "2.5",
    "PARAM_ENTRY_ZONE_ATR_MULT": "0.25",
    "PARAM_MIN_CONFIDENCE":      "0.55",
    "PARAM_MIN_SESSION_SCORE":   "0.55",
    # ── Signal: Circuit Breaker ───────────────────────────────────────────────
    "CIRCUIT_PAUSE_SEC":         "60",
    "CIRCUIT_KILL_COUNT":        "5",
    "PIPELINE_SIZE_FLOOR":       "0.5",

    # ── Tools: Pipeline Filters ───────────────────────────────────────────────
    "tool_enabled_session_filter":    "true",
    "MIN_SESSION_SCORE":              "0.55",
    "tool_enabled_news_filter":       "true",
    "NEWS_PRE_MINUTES":               "30",
    "NEWS_POST_MINUTES":              "15",
    "tool_enabled_volatility_filter": "true",
    "tool_enabled_dxy_filter":        "true",
    "tool_enabled_sentiment_filter":  "true",
    "tool_enabled_risk_filter":       "true",
    # ── Tools: Validation Rule Guards ────────────────────────────────────────
    "USE_EMA200_FILTER":          "true",
    "USE_BOS_GUARD":              "false",
    "BOS_MIN_BREAK_ATR":          "0.2",
    "BOS_SWING_LOOKBACK":         "20",
    "CHECK_FVG":                  "false",
    "FVG_MIN_SIZE_ATR":           "0.15",
    "FVG_LOOKBACK":               "20",
    "CHECK_TICK_JUMP":            "false",
    "TICK_JUMP_ATR_MAX":          "0.8",
    "CHECK_LIQ_VACUUM":           "false",
    "LIQ_VACUUM_SPIKE_MULT":      "2.5",
    "LIQ_VACUUM_BODY_PCT":        "30",
    "USE_VWAP_GUARD":             "false",
    "VWAP_EXTENSION_MAX_ATR":     "1.5",
    "USE_MACD_FILTER":            "false",
    "MACD_FAST":                  "12",
    "MACD_SLOW":                  "26",
    "MACD_SIGNAL":                "9",
    "USE_BOLLINGER_FILTER":       "false",
    "BB_PERIOD":                  "20",
    "BB_STD_DEV":                 "2.0",
    "USE_ADX_FILTER":             "false",
    "ADX_PERIOD":                 "14",
    "ADX_MIN_THRESHOLD":          "20",
    "USE_VOLUME_FILTER":          "false",
    "VOLUME_MA_PERIOD":           "20",
    "USE_SWING_STRUCTURE":        "false",
    # ── Tools: Stateful Guards ────────────────────────────────────────────────
    "GUARD_SIGNAL_HASH_WINDOW":      "3",
    "GUARD_CONF_VARIANCE_WINDOW":    "3",
    "GUARD_CONF_VARIANCE_MAX_STDEV": "0.15",
    "GUARD_SPREAD_REGIME_WINDOW":    "50",
    "GUARD_SPREAD_REGIME_THRESHOLD": "1.8",
    "GUARD_EQUITY_CURVE_WINDOW":     "20",
    "GUARD_EQUITY_CURVE_SCALE":      "0.5",
    "GUARD_DD_PAUSE_MINUTES":        "30",
    "GUARD_DD_PAUSE_LOOKBACK":       "3",
    "GUARD_PORTFOLIO_CAP_ENABLED":   "true",
    "USE_CORRELATION_GUARD":         "true",
    "CORRELATION_THRESHOLD_BLOCK":   "0.90",
    "CORRELATION_THRESHOLD_REDUCE":  "0.75",
    "GUARD_NEAR_DEDUP_ATR":          "1.0",
    # ── Tools: Position Management ────────────────────────────────────────────
    "REPOSITIONER_ENABLED":              "true",
    "REPOSITIONER_OPPOSITE_SIGNAL":      "true",
    "REPOSITIONER_NEWS_RISK":            "true",
    "REPOSITIONER_NEWS_WINDOW_MIN":      "15",
    "REPOSITIONER_VOLUME_SPIKE":         "true",
    "REPOSITIONER_VOLUME_SPIKE_MULT":    "2.5",
    "REPOSITIONER_VOLATILITY_SPIKE":     "true",
    "REPOSITIONER_VOLATILITY_SPIKE_MULT": "1.8",
    # ── Tools: Mode-Specific Overrides ───────────────────────────────────────
    "tool_enabled_risk_filter_dry_run":  "false",
    "tool_enabled_risk_filter_backtest": "false",
    "tool_enabled_risk_filter_live":     "true",
    # ── MetaLoop / AutoLearn ──────────────────────────────────────────────────
    "METALOOP_ENABLED":                "false",
    "METALOOP_CHECK_INTERVAL":         "20",
    "METALOOP_ROLLBACK_WINDOW":        "30",
    "METALOOP_AUTO_ACTIVATE":          "false",
    "METALOOP_DEGRADATION_THRESHOLD":  "0.7",
    # ── Health Monitor ────────────────────────────────────────────────────────
    "HEALTH_W_SHARPE":             "0.35",
    "HEALTH_W_WINRATE":            "0.25",
    "HEALTH_W_DRAWDOWN":           "0.25",
    "HEALTH_W_STAGNATION":         "0.15",
    "HEALTH_HEALTHY_THRESHOLD":    "0.6",
    "HEALTH_CRITICAL_THRESHOLD":   "0.3",
    # ── Confidence Sizing ─────────────────────────────────────────────────────
    "CONFIDENCE_SIZE_ENABLED":     "false",
    # ── Micro-Learning ────────────────────────────────────────────────────────
    "MICRO_LEARN_ENABLED":         "false",
    "MICRO_LEARN_MAX_PER_TRADE":   "0.01",
    "MICRO_LEARN_MAX_DRIFT":       "0.05",
    # ── Risk (referenced in Settings UI) ─────────────────────────────────────
    "RISK_PCT":                    "0.01",
    "LEVERAGE":                    "100",
    "CONTRACT_SIZE":               "100000",
    "COMMISSION_PER_LOT":          "7.0",
    "SL_SLIPPAGE_BUFFER":          "0.5",
    "MARGIN_CAP_PCT":              "0.20",
    "MAX_DAILY_LOSS_PCT":          "0.03",
    "MAX_CONCURRENT_TRADES":       "2",
    "CONSECUTIVE_LOSS_LIMIT":      "5",
    "MAX_SESSION_LOSS_PCT":        "0.05",
    "MAX_PORTFOLIO_HEAT_PCT":      "0.10",
    "RISK_SCORE_THRESHOLD":        "0.6",
    "MACRO_ABORT_THRESHOLD":       "0.8",
    # ── Session ──────────────────────────────────────────────────────────────
    "SESSION_LONDON_OPEN":         "07:00",
    "SESSION_LONDON_CLOSE":        "16:00",
    "SESSION_NY_OPEN":             "13:00",
    "SESSION_NY_CLOSE":            "21:00",
    "MIN_SPREAD_POINTS":           "2",
    # ── System ───────────────────────────────────────────────────────────────
    "DRY_RUN":                     "true",
    "LOG_LEVEL":                   "INFO",
    "ENVIRONMENT":                 "development",
    # ── WebUI Preferences ────────────────────────────────────────────────────
    "WEBUI_THEME":                 "dark",
}

# Keep old name as alias so any external references don't break.
SIGNAL_DEFAULTS = SETTING_DEFAULTS


class SettingsService:
    """
    Async settings service with in-memory cache.

    On first access (or after TTL expiry), reads all settings from DB
    into a dict cache. Individual get() calls hit the cache.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._cache: dict[str, str] = {}
        self._cache_loaded = False
        self._lock = asyncio.Lock()

    async def _ensure_cache(self) -> None:
        if self._cache_loaded:
            return
        async with self._lock:
            if self._cache_loaded:
                return
            await self._reload_cache()

    async def _reload_cache(self) -> None:
        try:
            async with self._session_factory() as session:
                result = await session.execute(select(AppSetting))
                self._cache = {
                    row.key: row.value or "" for row in result.scalars()
                }
                self._cache_loaded = True
        except Exception as e:
            logger.warning(f"[settings] Failed to load from DB: {e}")

    async def reload(self) -> None:
        """Force cache reload — call after saving settings via WebUI."""
        self._cache_loaded = False
        await self._reload_cache()

    async def get(self, key: str, default: str = "") -> str:
        await self._ensure_cache()
        return self._cache.get(key, default)

    async def get_float(self, key: str, default: float) -> float:
        raw = await self.get(key, str(default))
        try:
            return float(raw)
        except (ValueError, TypeError):
            logger.warning(f"[settings] Invalid float for '{key}': {raw}")
            return default

    async def get_int(self, key: str, default: int) -> int:
        raw = await self.get(key, str(default))
        try:
            return int(raw)
        except (ValueError, TypeError):
            logger.warning(f"[settings] Invalid int for '{key}': {raw}")
            return default

    async def get_bool(self, key: str, default: bool = True) -> bool:
        val = (await self.get(key, "")).strip().lower()
        if val in ("true", "1", "yes", "on"):
            return True
        if val in ("false", "0", "no", "off"):
            return False
        return default

    async def get_all(self) -> dict[str, str]:
        await self._ensure_cache()
        return dict(self._cache)

    async def set(self, key: str, value: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = value
            else:
                session.add(AppSetting(key=key, value=value))
            await session.commit()
        self._cache[key] = value

    async def set_many(self, settings: dict[str, str]) -> None:
        async with self._session_factory() as session:
            for key, value in settings.items():
                result = await session.execute(
                    select(AppSetting).where(AppSetting.key == key)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            await session.commit()
        self._cache.update(settings)

    async def seed_defaults(self, defaults: dict[str, str]) -> None:
        """Upsert default values for keys that are absent or empty in the DB.

        Only non-empty defaults overwrite existing empty-string entries —
        this fills fields that were saved blank without touching real user values.
        """
        await self._ensure_cache()
        to_seed = {
            k: v for k, v in defaults.items()
            if v and not self._cache.get(k)  # absent or empty-string in DB
        }
        # Also include keys whose default is explicitly "" but are absent
        to_seed.update({
            k: v for k, v in defaults.items()
            if not v and k not in self._cache
        })
        if not to_seed:
            return
        async with self._session_factory() as session:
            for key, value in to_seed.items():
                result = await session.execute(
                    select(AppSetting).where(AppSetting.key == key)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.value = value
                else:
                    session.add(AppSetting(key=key, value=value))
            await session.commit()
        self._cache.update(to_seed)
        logger.info("[settings] Seeded %d default(s): %s", len(to_seed), list(to_seed))

    async def delete(self, key: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                await session.delete(existing)
                await session.commit()
        self._cache.pop(key, None)
