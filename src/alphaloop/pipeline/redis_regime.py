"""
pipeline/redis_regime.py — Redis-backed regime state persistence.

Persists RegimeClassifier EWM state and regime transition history
to Redis for cross-session learning.  On startup, pulls cached state
so new instances skip cold-start regime flips.

Keys:
    alphaloop:regime:{instance_id}:{symbol}     — EWM state (TTL 24h)
    alphaloop:regime_history:{symbol}            — transition log (TTL 7d, 50 records)

All Redis operations are fire-and-forget — never blocks trading.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alphaloop.pipeline.regime import RegimeClassifier

logger = logging.getLogger(__name__)

_STATE_TTL = 86400       # 24 hours
_HISTORY_TTL = 604800    # 7 days
_HISTORY_MAX = 50


class RedisRegimePersistence:
    """Async Redis cache for RegimeClassifier state."""

    def __init__(self, redis_client: Any, instance_id: str = "default") -> None:
        self._client = redis_client
        self._instance_id = instance_id
        self._last_regime: dict[str, str] = {}  # symbol → last pushed regime

    def _state_key(self, symbol: str) -> str:
        return f"alphaloop:regime:{self._instance_id}:{symbol}"

    def _history_key(self, symbol: str) -> str:
        return f"alphaloop:regime_history:{symbol}"

    async def push_state(self, classifier: "RegimeClassifier", symbol: str) -> bool:
        """Push EWM state + detect/log regime transitions."""
        if self._client is None:
            return False
        try:
            state = classifier.state
            state["_pushed_at"] = datetime.now(timezone.utc).isoformat()
            await self._client.set(
                self._state_key(symbol),
                json.dumps(state),
                ex=_STATE_TTL,
            )

            # Detect regime transition
            current = max(state.get("smoothed", {}), key=lambda r: state["smoothed"][r], default="neutral")
            prev = self._last_regime.get(symbol)
            if prev and prev != current:
                record = json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "from_regime": prev,
                    "to_regime": current,
                    "smoothed": state.get("smoothed", {}),
                })
                hkey = self._history_key(symbol)
                await self._client.lpush(hkey, record)
                await self._client.ltrim(hkey, 0, _HISTORY_MAX - 1)
                await self._client.expire(hkey, _HISTORY_TTL)
                logger.info("[redis-regime] %s transition: %s → %s", symbol, prev, current)

            self._last_regime[symbol] = current
            return True
        except Exception as e:
            logger.warning("[redis-regime] push_state failed: %s", e)
            return False

    async def pull_state(self, classifier: "RegimeClassifier", symbol: str) -> bool:
        """Restore EWM state from Redis on startup."""
        if self._client is None:
            return False
        try:
            raw = await self._client.get(self._state_key(symbol))
            if not raw:
                return False
            cached = json.loads(raw)
            pushed_at = cached.get("_pushed_at")
            if pushed_at:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(pushed_at)).total_seconds()
                if age > _STATE_TTL:
                    logger.info("[redis-regime] Cached state too old (%.0fs) — ignoring", age)
                    return False
            classifier.load_state(cached)
            logger.info("[redis-regime] Restored regime state for %s (age=%.0fs)", symbol, age if pushed_at else 0)
            return True
        except Exception as e:
            logger.warning("[redis-regime] pull_state failed: %s", e)
            return False

    async def get_history(self, symbol: str, count: int = 20) -> list[dict[str, Any]]:
        """Read recent regime transitions for analytics."""
        if self._client is None:
            return []
        try:
            raw_list = await self._client.lrange(self._history_key(symbol), 0, count - 1)
            return [json.loads(r) for r in raw_list]
        except Exception as e:
            logger.warning("[redis-regime] get_history failed: %s", e)
            return []
