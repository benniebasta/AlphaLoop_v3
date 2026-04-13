"""
pipeline/redis_ece.py — Redis-backed ECE calibration bin persistence.

Persists ECE calibration bins across sessions so that ai_weight starts
at the calibrated value instead of cold-starting at 0.50.

Keys:
    alphaloop:ece:{instance_id}:{symbol}  — ECE bins + ai_weight (TTL 7d)

Records older than 7 days are discarded.  Minimum 10 outcomes before
ECE influences ai_weight.

All Redis operations are fire-and-forget — never blocks trading.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_ECE_TTL = 604800  # 7 days
_MIN_OUTCOMES = 10


class RedisECEPersistence:
    """Async Redis cache for ECE calibration state."""

    def __init__(self, redis_client: Any, instance_id: str = "default") -> None:
        self._client = redis_client
        self._instance_id = instance_id

    def _key(self, symbol: str) -> str:
        return f"alphaloop:ece:{self._instance_id}:{symbol}"

    async def push_state(
        self,
        symbol: str,
        *,
        bins: list[dict[str, Any]] | None = None,
        ai_weight: float = 0.50,
        ece_score: float = 0.0,
    ) -> bool:
        """Push ECE calibration state to Redis."""
        if self._client is None:
            return False
        try:
            payload = {
                "bins": bins or [],
                "ai_weight": ai_weight,
                "ece_score": ece_score,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await self._client.set(
                self._key(symbol),
                json.dumps(payload),
                ex=_ECE_TTL,
            )
            return True
        except Exception as e:
            logger.warning("[redis-ece] push_state failed: %s", e)
            return False

    async def pull_state(self, symbol: str) -> dict[str, Any] | None:
        """Pull ECE calibration state from Redis.

        Returns None if no cached state or too old.
        Returns dict with keys: bins, ai_weight, ece_score, updated_at.
        """
        if self._client is None:
            return None
        try:
            raw = await self._client.get(self._key(symbol))
            if not raw:
                return None
            cached = json.loads(raw)

            # Age check
            updated_at = cached.get("updated_at")
            if updated_at:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds()
                if age > _ECE_TTL:
                    logger.info("[redis-ece] Cached ECE too old (%.0fs) — ignoring", age)
                    return None

            # Minimum outcomes check
            total_outcomes = sum(b.get("count", 0) for b in cached.get("bins", []))
            if total_outcomes < _MIN_OUTCOMES:
                logger.debug(
                    "[redis-ece] Only %d outcomes (need %d) — returning state but flagging immature",
                    total_outcomes, _MIN_OUTCOMES,
                )
                cached["immature"] = True
            else:
                cached["immature"] = False

            logger.info(
                "[redis-ece] Restored ECE state for %s: ai_weight=%.3f ece=%.4f outcomes=%d",
                symbol, cached.get("ai_weight", 0.5), cached.get("ece_score", 0), total_outcomes,
            )
            return dict(cached)
        except Exception as e:
            logger.warning("[redis-ece] pull_state failed: %s", e)
            return None

    async def record_outcome(
        self,
        symbol: str,
        predicted_confidence: float,
        actual_win: bool,
    ) -> bool:
        """Record a trade outcome into the ECE bins.

        Bins are 10 equal-width intervals [0.0-0.1), [0.1-0.2), ..., [0.9-1.0].
        Each bin tracks: predicted_sum, actual_sum, count.
        """
        if self._client is None:
            return False
        try:
            raw = await self._client.get(self._key(symbol))
            if raw:
                state = json.loads(raw)
            else:
                state = {"bins": [], "ai_weight": 0.50, "ece_score": 0.0}

            bins = state.get("bins") or []
            # Ensure 10 bins
            while len(bins) < 10:
                bins.append({"predicted_sum": 0.0, "actual_sum": 0.0, "count": 0})

            # Find correct bin
            bin_idx = min(int(predicted_confidence * 10), 9)
            bins[bin_idx]["predicted_sum"] += predicted_confidence
            bins[bin_idx]["actual_sum"] += 1.0 if actual_win else 0.0
            bins[bin_idx]["count"] += 1

            # Recompute ECE
            total = sum(b["count"] for b in bins)
            ece = 0.0
            if total >= _MIN_OUTCOMES:
                for b in bins:
                    if b["count"] > 0:
                        avg_pred = b["predicted_sum"] / b["count"]
                        avg_actual = b["actual_sum"] / b["count"]
                        ece += abs(avg_pred - avg_actual) * (b["count"] / total)

            state["bins"] = bins
            state["ece_score"] = round(ece, 6)
            state["updated_at"] = datetime.now(timezone.utc).isoformat()

            await self._client.set(
                self._key(symbol),
                json.dumps(state),
                ex=_ECE_TTL,
            )
            return True
        except Exception as e:
            logger.warning("[redis-ece] record_outcome failed: %s", e)
            return False
