"""
In-memory ring buffer for operational metrics.
Records per-5-minute buckets. No external dependencies.

Metrics tracked:
  - signal_latency_ms
  - validation_latency_ms
  - rejection_count / approval_count
  - slippage_pips
  - cycle_duration_ms
"""

import asyncio
import threading
import time
from collections import defaultdict

_BUCKET_SIZE_SEC = 300  # 5 minutes
_MAX_BUCKETS = 288  # 24 hours of 5-min buckets

# {bucket_ts: {metric_name: [values]}}
_buckets: dict[int, dict[str, list[float]]] = {}
_bucket_order: list[int] = []
_lock = asyncio.Lock()
_thread_lock = threading.Lock()


def _current_bucket() -> int:
    return int(time.time()) // _BUCKET_SIZE_SEC * _BUCKET_SIZE_SEC


async def record(metric: str, value: float) -> None:
    """Record a metric value into the current time bucket (async)."""
    bucket = _current_bucket()
    async with _lock:
        if bucket not in _buckets:
            _buckets[bucket] = defaultdict(list)
            _bucket_order.append(bucket)
            while len(_bucket_order) > _MAX_BUCKETS:
                old = _bucket_order.pop(0)
                _buckets.pop(old, None)
        _buckets[bucket][metric].append(value)


def record_sync(metric: str, value: float) -> None:
    """Record a metric (sync version for non-async contexts)."""
    bucket = _current_bucket()
    with _thread_lock:
        if bucket not in _buckets:
            _buckets[bucket] = defaultdict(list)
            _bucket_order.append(bucket)
            while len(_bucket_order) > _MAX_BUCKETS:
                old = _bucket_order.pop(0)
                _buckets.pop(old, None)
        _buckets[bucket][metric].append(value)


def get_timeseries(hours: int = 24) -> list[dict]:
    """Return aggregated metrics per bucket for the last N hours."""
    cutoff = _current_bucket() - (hours * 3600)
    result = []
    for ts in _bucket_order:
        if ts < cutoff:
            continue
        entry = {"timestamp": ts, "bucket_start": ts}
        for metric, values in _buckets[ts].items():
            entry[f"{metric}_count"] = len(values)
            entry[f"{metric}_avg"] = round(sum(values) / len(values), 2) if values else 0
            entry[f"{metric}_max"] = round(max(values), 2) if values else 0
            entry[f"{metric}_min"] = round(min(values), 2) if values else 0
        result.append(entry)
    return result


def get_latest() -> dict:
    """Get the latest bucket's metrics."""
    if not _bucket_order:
        return {}
    ts = _bucket_order[-1]
    entry = {"timestamp": ts}
    for metric, values in _buckets[ts].items():
        entry[f"{metric}_count"] = len(values)
        entry[f"{metric}_avg"] = round(sum(values) / len(values), 2) if values else 0
    return entry


def reset() -> None:
    """Reset all metrics."""
    _buckets.clear()
    _bucket_order.clear()
