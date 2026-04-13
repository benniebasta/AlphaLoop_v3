"""
risk/redis_guards.py — Redis-backed guard state + rejection stats persistence.

Persists guard filter state to Redis with TTL so that dedup filters
don't allow duplicate signals after restart.  Also stores rejection
analytics (7-day, 50-record retention) for post-hoc analysis.

Keys:
    alphaloop:guard:signal_hash:{symbol}     — SignalHashFilter history (TTL 24h)
    alphaloop:guard:conf_var:{symbol}        — ConfidenceVarianceFilter history (TTL 24h)
    alphaloop:rejections:{symbol}            — Rejection log (TTL 7d, 50 records)

All Redis operations are fire-and-forget — never blocks trading.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_GUARD_TTL = 86400       # 24 hours
_REJECTION_TTL = 604800  # 7 days
_REJECTION_MAX = 50


class RedisGuardPersistence:
    """Async Redis cache for guard filter state and rejection analytics."""

    def __init__(self, redis_client: Any) -> None:
        self._client = redis_client

    def _hash_key(self, symbol: str) -> str:
        return f"alphaloop:guard:signal_hash:{symbol}"

    def _conf_key(self, symbol: str) -> str:
        return f"alphaloop:guard:conf_var:{symbol}"

    def _rejection_key(self, symbol: str) -> str:
        return f"alphaloop:rejections:{symbol}"

    async def push_state(
        self,
        signal_hash_filter: Any,
        conf_variance_filter: Any,
        symbol: str,
    ) -> bool:
        """Push guard filter state to Redis."""
        if self._client is None:
            return False
        try:
            # SignalHashFilter — deque of recent hashes
            if signal_hash_filter and hasattr(signal_hash_filter, "_hashes"):
                hash_data = {
                    "hashes": list(signal_hash_filter._hashes),
                    "window": signal_hash_filter.window,
                    "_pushed_at": datetime.now(timezone.utc).isoformat(),
                }
                await self._client.set(
                    self._hash_key(symbol),
                    json.dumps(hash_data),
                    ex=_GUARD_TTL,
                )

            # ConfidenceVarianceFilter — deque of recent confidences
            if conf_variance_filter and hasattr(conf_variance_filter, "_confs"):
                conf_data = {
                    "confs": list(conf_variance_filter._confs),
                    "window": conf_variance_filter.window,
                    "_pushed_at": datetime.now(timezone.utc).isoformat(),
                }
                await self._client.set(
                    self._conf_key(symbol),
                    json.dumps(conf_data),
                    ex=_GUARD_TTL,
                )

            return True
        except Exception as e:
            logger.warning("[redis-guards] push_state failed: %s", e)
            return False

    async def pull_state(
        self,
        signal_hash_filter: Any,
        conf_variance_filter: Any,
        symbol: str,
    ) -> bool:
        """Restore guard filter state from Redis on startup."""
        if self._client is None:
            return False
        restored = False
        try:
            # SignalHashFilter
            raw = await self._client.get(self._hash_key(symbol))
            if raw and signal_hash_filter:
                data = json.loads(raw)
                from collections import deque
                signal_hash_filter._hashes = deque(
                    data.get("hashes", []),
                    maxlen=signal_hash_filter.window,
                )
                restored = True
                logger.info("[redis-guards] Restored signal hash state for %s (%d hashes)", symbol, len(signal_hash_filter._hashes))

            # ConfidenceVarianceFilter
            raw = await self._client.get(self._conf_key(symbol))
            if raw and conf_variance_filter:
                data = json.loads(raw)
                from collections import deque
                conf_variance_filter._confs = deque(
                    [float(c) for c in data.get("confs", [])],
                    maxlen=conf_variance_filter.window,
                )
                restored = True
                logger.info("[redis-guards] Restored confidence variance state for %s (%d records)", symbol, len(conf_variance_filter._confs))

            return restored
        except Exception as e:
            logger.warning("[redis-guards] pull_state failed: %s", e)
            return False

    async def log_rejection(
        self,
        symbol: str,
        *,
        direction: str = "",
        setup_type: str = "",
        timeframe: str = "",
        sl_source: str = "",
        rejected_by: str = "",
        reason: str = "",
    ) -> bool:
        """Log a pipeline rejection for analytics."""
        if self._client is None:
            return False
        try:
            record = json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "direction": direction,
                "setup_type": setup_type,
                "timeframe": timeframe,
                "sl_source": sl_source,
                "rejected_by": rejected_by,
                "reason": reason,
            })
            rkey = self._rejection_key(symbol)
            await self._client.lpush(rkey, record)
            await self._client.ltrim(rkey, 0, _REJECTION_MAX - 1)
            await self._client.expire(rkey, _REJECTION_TTL)
            return True
        except Exception as e:
            logger.warning("[redis-guards] log_rejection failed: %s", e)
            return False

    async def get_rejections(self, symbol: str, count: int = 50) -> list[dict[str, Any]]:
        """Read recent rejection records for analytics."""
        if self._client is None:
            return []
        try:
            raw_list = await self._client.lrange(self._rejection_key(symbol), 0, count - 1)
            return [json.loads(r) for r in raw_list]
        except Exception as e:
            logger.warning("[redis-guards] get_rejections failed: %s", e)
            return []
