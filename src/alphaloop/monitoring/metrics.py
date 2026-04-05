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


def get_prometheus_text() -> str:
    """
    Export metrics in Prometheus text exposition format.
    Can be served at /metrics endpoint for scraping.
    """
    lines: list[str] = []
    latest = get_latest()
    if not latest:
        return "# No metrics available\n"

    for key, value in latest.items():
        if key == "timestamp":
            continue
        # Convert metric_name_stat to prometheus format
        prom_name = f"alphaloop_{key}"
        prom_name = prom_name.replace(".", "_").replace("-", "_")
        lines.append(f"{prom_name} {value}")

    return "\n".join(lines) + "\n"


async def persist_to_db(settings_service) -> None:
    """Persist current metrics snapshot to DB for survival across restarts."""
    import json
    snapshot = {
        "latest": get_latest(),
        "timestamp": int(time.time()),
    }
    try:
        await settings_service.set("metrics_snapshot", json.dumps(snapshot))
    except Exception:
        pass  # Best-effort persistence


async def restore_from_db(settings_service) -> None:
    """Restore metrics from DB snapshot (best-effort, metrics are approximate)."""
    import json
    try:
        raw = await settings_service.get("metrics_snapshot")
        if raw:
            snapshot = json.loads(raw)
            # Only informational — we don't restore individual values
            # as that would corrupt the ring buffer structure.
            # Just log that we have historical data.
            ts = snapshot.get("timestamp", 0)
            if ts:
                import logging
                logging.getLogger(__name__).info(
                    "[metrics] Previous snapshot from ts=%d available", ts
                )
    except Exception:
        pass


class MetricsTracker:
    """
    Singleton wrapper around the module-level metrics functions.
    Provides an object-oriented interface while delegating to the
    existing ring-buffer implementation.

    Usage:
        from alphaloop.monitoring.metrics import metrics_tracker
        metrics_tracker.record_sync("cycle_duration_ms", 123.4)
        text = metrics_tracker.get_prometheus_text()
    """

    def record_sync(self, metric: str, value: float) -> None:
        """Record a metric value synchronously (thread-safe)."""
        record_sync(metric, value)

    def record_portfolio_risk(
        self,
        corr_adj_risk_pct: float,
        simple_risk_pct: float,
        n_trades: int,
        balance: float,
    ) -> None:
        """
        Record a portfolio risk snapshot.

        Stores both correlation-adjusted and simple risk metrics so the
        WebUI and Prometheus can display portfolio heat over time.

        Args:
            corr_adj_risk_pct: Correlation-adjusted portfolio risk as % of balance
            simple_risk_pct:   Simple sum risk as % of balance
            n_trades:          Number of open trades
            balance:           Account balance at snapshot time
        """
        record_sync("portfolio_risk_corr_adj_pct", corr_adj_risk_pct)
        record_sync("portfolio_risk_simple_pct", simple_risk_pct)
        record_sync("portfolio_open_trades", float(n_trades))
        import logging
        logging.getLogger(__name__).debug(
            "[portfolio-risk] corr_adj=%.2f%% simple=%.2f%% trades=%d balance=$%.0f",
            corr_adj_risk_pct, simple_risk_pct, n_trades, balance,
        )

    async def record(self, metric: str, value: float) -> None:
        """Record a metric value asynchronously."""
        await record(metric, value)

    def get_timeseries(self, hours: int = 24) -> list[dict]:
        """Return aggregated metrics for the last N hours."""
        return get_timeseries(hours)

    def get_latest(self) -> dict:
        """Return the latest bucket's metrics."""
        return get_latest()

    def get_prometheus_text(self) -> str:
        """Return Prometheus text exposition format."""
        return get_prometheus_text()

    async def persist_to_db(self, settings_service) -> None:
        """Persist current metrics snapshot to DB."""
        await persist_to_db(settings_service)

    async def restore_from_db(self, settings_service) -> None:
        """Restore metrics from DB snapshot."""
        await restore_from_db(settings_service)

    def reset(self) -> None:
        """Reset all metrics."""
        reset()


# Module-level singleton — importable as `from alphaloop.monitoring.metrics import metrics_tracker`
metrics_tracker = MetricsTracker()
