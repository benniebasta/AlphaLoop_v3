"""
ai/performance.py — Per-model performance tracker for the AI caller.

Tracks latency, error rate, and call count per model_id.
Exposes a summary for the /api/ai-hub/performance endpoint.

Usage:
    from alphaloop.ai.performance import model_performance_tracker
    model_performance_tracker.record_call("claude-haiku", latency_ms=250, success=True)
    summary = model_performance_tracker.get_summary()
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

# Maximum call records kept per model (FIFO ring buffer)
_MAX_RECORDS = 200


class ModelPerformanceTracker:
    """
    Thread-safe in-memory tracker for AI model call performance.
    Maintains per-model rolling windows of latency and outcome data.
    """

    def __init__(self, max_records: int = _MAX_RECORDS) -> None:
        self._max_records = max_records
        # Per-model ring buffers
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self._max_records))
        self._successes: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=self._max_records))
        self._call_counts: dict[str, int] = defaultdict(int)

    def record_call(
        self,
        model_id: str,
        latency_ms: float,
        success: bool,
        outcome: str | None = None,
    ) -> None:
        """
        Record a single AI call result.

        Parameters
        ----------
        model_id : str
            Model identifier (e.g. "claude-haiku-4-5-20251001").
        latency_ms : float
            Round-trip latency in milliseconds.
        success : bool
            True if the call returned a valid response, False on error/timeout.
        outcome : str or None
            Optional outcome label (e.g. "approved", "rejected") for accuracy tracking.
        """
        self._latencies[model_id].append(latency_ms)
        self._successes[model_id].append(success)
        self._call_counts[model_id] += 1

    def get_summary(self) -> dict[str, Any]:
        """
        Return per-model performance summary.

        Returns
        -------
        dict with structure:
            {
                "models": {
                    "model-id": {
                        "call_count": int,
                        "avg_latency_ms": float,
                        "p95_latency_ms": float,
                        "error_rate": float,   # 0.0 - 1.0
                        "success_rate": float, # 0.0 - 1.0
                    },
                    ...
                },
                "worst_model": str | None,
                "total_calls": int,
            }
        """
        result: dict[str, Any] = {}
        all_model_ids = set(self._latencies.keys()) | set(self._successes.keys())

        for model_id in all_model_ids:
            lats = list(self._latencies[model_id])
            succs = list(self._successes[model_id])

            avg_lat = round(statistics.mean(lats), 1) if lats else 0.0
            p95_lat = 0.0
            if len(lats) >= 2:
                sorted_lats = sorted(lats)
                idx = max(0, int(len(sorted_lats) * 0.95) - 1)
                p95_lat = round(sorted_lats[idx], 1)
            elif lats:
                p95_lat = round(lats[-1], 1)

            total = len(succs)
            errors = sum(1 for s in succs if not s)
            error_rate = round(errors / total, 4) if total > 0 else 0.0
            success_rate = round(1.0 - error_rate, 4)

            result[model_id] = {
                "call_count": self._call_counts[model_id],
                "avg_latency_ms": avg_lat,
                "p95_latency_ms": p95_lat,
                "error_rate": error_rate,
                "success_rate": success_rate,
            }

        # Identify worst model (highest error rate, min 10 calls)
        worst_model = self.get_worst_model()

        return {
            "models": result,
            "worst_model": worst_model,
            "total_calls": sum(self._call_counts.values()),
        }

    def get_worst_model(self, min_calls: int = 10) -> str | None:
        """
        Return the model_id with the highest error rate (minimum N calls).
        Returns None if no model meets the minimum call threshold.
        """
        worst_id: str | None = None
        worst_rate: float = -1.0

        for model_id, succs in self._successes.items():
            total = len(succs)
            if total < min_calls:
                continue
            errors = sum(1 for s in succs if not s)
            error_rate = errors / total
            if error_rate > worst_rate:
                worst_rate = error_rate
                worst_id = model_id

        return worst_id

    def reset(self) -> None:
        """Clear all recorded data."""
        self._latencies.clear()
        self._successes.clear()
        self._call_counts.clear()


# Module-level singleton
model_performance_tracker = ModelPerformanceTracker()
