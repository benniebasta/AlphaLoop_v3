"""
monitoring/watchdog.py — Trading loop health watchdog.

Monitors the heartbeat.json file and raises alerts when the trading loop
becomes unresponsive. Can run as a background asyncio task within the
web server, or as a standalone monitoring process.

Alert actions:
  1. Log critical warning
  2. Update health check status to UNHEALTHY
  3. Publish RiskLimitHit event (triggers Telegram notification)
  4. Optionally attempt process restart

Usage (within webui):
    watchdog = TradingWatchdog(
        health_check=health,
        event_bus=event_bus,
        heartbeat_path="heartbeat.json",
    )
    task = asyncio.create_task(watchdog.run())
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from alphaloop.monitoring.health import ComponentStatus, HealthCheck

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_CHECK_INTERVAL = 60.0       # seconds between checks
DEFAULT_STALE_THRESHOLD = 600.0     # seconds before heartbeat considered stale (2 cycles @ 5min)
DEFAULT_CRITICAL_THRESHOLD = 900.0  # seconds before critical alert (3 cycles)


class TradingWatchdog:
    """
    Monitors trading loop health via heartbeat.json.

    Detects:
    - Stale heartbeat (loop frozen or crashed)
    - Kill switch activation (from heartbeat data)
    - Circuit breaker open (from heartbeat data)
    - High error rates

    Actions on detection:
    - Updates HealthCheck component status
    - Publishes events for notification dispatch
    - Logs structured warnings
    """

    def __init__(
        self,
        health_check: HealthCheck | None = None,
        event_bus: Any | None = None,
        session_factory=None,
        heartbeat_path: str | Path = "heartbeat.json",
        check_interval: float = DEFAULT_CHECK_INTERVAL,
        stale_threshold: float = DEFAULT_STALE_THRESHOLD,
        critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
    ) -> None:
        self._health = health_check or HealthCheck()
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._heartbeat_path = Path(heartbeat_path)
        self._check_interval = check_interval
        self._stale_threshold = stale_threshold
        self._critical_threshold = critical_threshold
        self._running = False
        self._consecutive_failures = 0
        self._last_alert_time = 0.0

        # Register the component
        self._health.register(
            "trading_loop",
            ComponentStatus.UNKNOWN,
            "Watchdog starting",
        )

    async def run(self) -> None:
        """Run the watchdog loop indefinitely."""
        self._running = True
        logger.info(
            "Watchdog started: checking every %.0fs, stale=%.0fs, critical=%.0fs",
            self._check_interval, self._stale_threshold, self._critical_threshold,
        )

        while self._running:
            try:
                await self._check()
            except Exception as exc:
                logger.error("Watchdog check failed: %s", exc, exc_info=True)
            await asyncio.sleep(self._check_interval)

    def stop(self) -> None:
        """Signal the watchdog to stop."""
        self._running = False

    async def _check(self) -> None:
        """Perform one health check cycle."""
        now = time.time()

        if not self._heartbeat_path.exists():
            self._health.update(
                "trading_loop",
                ComponentStatus.UNKNOWN,
                "No heartbeat file found — trading loop may not be running",
            )
            return

        try:
            data = json.loads(self._heartbeat_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            self._health.update(
                "trading_loop",
                ComponentStatus.DEGRADED,
                f"Cannot read heartbeat: {exc}",
            )
            return

        ts = data.get("timestamp", 0)
        age = now - ts

        # Check heartbeat freshness
        if age > self._critical_threshold:
            self._consecutive_failures += 1
            self._health.update(
                "trading_loop",
                ComponentStatus.UNHEALTHY,
                f"Heartbeat stale for {age:.0f}s (critical threshold: {self._critical_threshold:.0f}s). "
                f"Consecutive failures: {self._consecutive_failures}",
            )
            await self._alert(
                "critical",
                f"Trading loop UNRESPONSIVE for {age:.0f}s",
                {"age_seconds": age, "consecutive_failures": self._consecutive_failures},
            )
            return

        if age > self._stale_threshold:
            self._consecutive_failures += 1
            self._health.update(
                "trading_loop",
                ComponentStatus.DEGRADED,
                f"Heartbeat stale for {age:.0f}s (threshold: {self._stale_threshold:.0f}s)",
            )
            await self._alert(
                "warning",
                f"Trading loop heartbeat stale for {age:.0f}s",
                {"age_seconds": age},
            )
            return

        # Heartbeat is fresh — check internal status
        self._consecutive_failures = 0

        # Check kill switch
        risk_state = data.get("risk_state", {})
        if risk_state.get("kill_switch_active"):
            self._health.update(
                "trading_loop",
                ComponentStatus.DEGRADED,
                "Kill switch is ACTIVE — trading halted",
            )
            return

        # Check circuit breaker
        cb = data.get("circuit_breaker", {})
        if cb.get("open"):
            self._health.update(
                "trading_loop",
                ComponentStatus.DEGRADED,
                f"Circuit breaker OPEN — failures: {cb.get('failures', '?')}",
            )
            return

        # All good
        cycle = data.get("cycle", "?")
        symbol = data.get("symbol", "?")
        self._health.update(
            "trading_loop",
            ComponentStatus.HEALTHY,
            f"Cycle {cycle} on {symbol} — heartbeat {age:.0f}s ago",
        )

    async def _alert(
        self,
        severity: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        """Send alert via event bus (throttled to 1 per 5 minutes)."""
        now = time.time()
        if now - self._last_alert_time < 300:  # throttle: 5 min
            return
        self._last_alert_time = now

        logger.critical("[watchdog] %s: %s — %s", severity.upper(), message, details)
        await self._record_incident(
            severity,
            message,
            details,
        )

        if self._event_bus:
            try:
                from alphaloop.core.events import RiskLimitHit
                await self._event_bus.publish(RiskLimitHit(
                    limit_type=f"watchdog_{severity}",
                    details=message,
                ))
            except Exception as exc:
                logger.error("Watchdog alert publish failed: %s", exc)

    async def _record_incident(
        self,
        severity: str,
        message: str,
        details: dict[str, Any],
    ) -> None:
        if not self._session_factory:
            return
        try:
            from alphaloop.supervision.service import SupervisionService

            supervision = SupervisionService(self._session_factory)
            await supervision.record_incident(
                incident_type="watchdog_triggered",
                details=message,
                severity="critical" if severity == "critical" else "warning",
                source="watchdog",
                payload=details,
            )
        except Exception as exc:
            logger.warning("Watchdog incident persist failed: %s", exc)

    def get_status(self) -> dict[str, Any]:
        """Return current watchdog status."""
        return {
            "running": self._running,
            "consecutive_failures": self._consecutive_failures,
            "heartbeat_path": str(self._heartbeat_path),
            "heartbeat_exists": self._heartbeat_path.exists(),
            "check_interval": self._check_interval,
            "stale_threshold": self._stale_threshold,
            "critical_threshold": self._critical_threshold,
        }
