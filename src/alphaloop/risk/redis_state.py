"""
risk/redis_state.py — Redis-backed risk state replication for HA.

Replicates in-memory RiskMonitor state to Redis every N cycles
so that a crashed instance can recover faster than waiting for
a full DB seed on restart.

IMPORTANT: SQLite/Postgres remains authoritative. Redis is a
fast-read cache layer only. guard_persistence.py continues to
write to DB; this module is additive.

Usage:
    redis_sync = RedisStateSync(redis_url="redis://localhost:6379")
    if await redis_sync.connect():
        await redis_sync.push_risk_state(monitor)
        await redis_sync.pull_risk_state(monitor)  # on restart

Enabled only when REDIS_URL env var is set (see main.py).
Falls back gracefully if Redis is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alphaloop.risk.monitor import RiskMonitor

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "alphaloop:state"
_DEFAULT_TTL = 3600  # 1 hour


class RedisStateSync:
    """
    Async Redis state cache for RiskMonitor.

    Parameters
    ----------
    redis_url : str
        Redis connection URL, e.g. "redis://localhost:6379".
    instance_id : str
        Unique identifier for this AlphaLoop instance (used as key suffix).
    ttl_seconds : int
        Key TTL in Redis. Defaults to 3600 (1 hour).
    """

    def __init__(
        self,
        redis_url: str,
        instance_id: str = "default",
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        self._url = redis_url
        self._instance_id = instance_id
        self._ttl = ttl_seconds
        self._client = None
        self._key = f"{_REDIS_KEY_PREFIX}:{instance_id}"

    async def connect(self) -> bool:
        """
        Open Redis connection. Returns False if unavailable — never raises.

        Returns
        -------
        bool
            True if connected successfully, False otherwise.
        """
        try:
            import redis.asyncio as aioredis  # type: ignore[import]
            self._client = aioredis.from_url(self._url, decode_responses=True)
            await asyncio.wait_for(self._client.ping(), timeout=3.0)
            logger.info("[redis] Connected to %s | key=%s", self._url, self._key)
            return True
        except Exception as e:
            logger.warning("[redis] Connection failed (HA disabled): %s", e)
            self._client = None
            return False

    async def push_risk_state(self, monitor: "RiskMonitor") -> bool:
        """
        Serialize and push RiskMonitor state to Redis.

        Parameters
        ----------
        monitor : RiskMonitor

        Returns
        -------
        bool
            True on success.
        """
        if self._client is None:
            return False
        try:
            state = monitor.status
            state["_synced_at"] = datetime.now(timezone.utc).isoformat()
            payload = json.dumps(state)
            await self._client.set(self._key, payload, ex=self._ttl)
            logger.debug("[redis] Risk state pushed | key=%s", self._key)
            return True
        except Exception as e:
            logger.warning("[redis] push_risk_state failed: %s", e)
            return False

    async def pull_risk_state(self, monitor: "RiskMonitor") -> bool:
        """
        Read Redis cache and apply advisory values to monitor (non-destructive).

        Only applies values if the cached state is newer than the current
        seeded state. Does NOT override kill_switch or force_close_all from
        DB — those remain authoritative from guard_persistence.py.

        Parameters
        ----------
        monitor : RiskMonitor

        Returns
        -------
        bool
            True if cache was found and applied.
        """
        if self._client is None:
            return False
        try:
            raw = await self._client.get(self._key)
            if not raw:
                logger.debug("[redis] No cached state found for key=%s", self._key)
                return False

            cached = json.loads(raw)
            synced_at_str = cached.get("_synced_at")
            if synced_at_str:
                synced_at = datetime.fromisoformat(synced_at_str)
                age_seconds = (datetime.now(timezone.utc) - synced_at).total_seconds()
                if age_seconds > self._ttl:
                    logger.info("[redis] Cached state too old (%.0fs) — ignoring", age_seconds)
                    return False

            # Apply non-authoritative counters as advisory restore
            # DB seed is still required for kill_switch / guard state
            if not monitor._seeded:
                if "daily_pnl" in cached:
                    monitor._daily_pnl = float(cached["daily_pnl"])
                if "consecutive_losses" in cached:
                    monitor._consecutive_losses = int(cached["consecutive_losses"])
                if "open_trades" in cached:
                    monitor._open_trades = int(cached["open_trades"])
                if "open_risk_usd" in cached:
                    monitor._open_risk_usd = float(cached["open_risk_usd"])
                logger.info(
                    "[redis] Applied cached risk state (age=%.0fs) | "
                    "daily_pnl=%.2f consec=%d open=%d",
                    age_seconds if synced_at_str else 0,
                    monitor._daily_pnl,
                    monitor._consecutive_losses,
                    monitor._open_trades,
                )
                return True

            logger.debug("[redis] Monitor already seeded from DB — skipping cache apply")
            return False

        except Exception as e:
            logger.warning("[redis] pull_risk_state failed: %s", e)
            return False

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
