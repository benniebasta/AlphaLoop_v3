"""
Dead-man's-switch for live trading safety.

Runs as an independent async task that monitors the heartbeat file.
If the trading loop goes silent for too long, triggers emergency
actions: alerts, position close requests, and kill switch activation.

This is the last line of defense against unmonitored system failures.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Default thresholds
HEARTBEAT_WARNING_SEC = 600     # 10 minutes — warning
HEARTBEAT_CRITICAL_SEC = 900    # 15 minutes — critical, trigger close
HEARTBEAT_EMERGENCY_SEC = 1800  # 30 minutes — emergency, escalate

DEFAULT_HEARTBEAT_PATH = "heartbeat.json"


class DeadManSwitch:
    """
    Monitors heartbeat file and triggers emergency actions on silence.

    Independent from the trading loop — designed to detect when the
    trading loop itself has died.
    """

    def __init__(
        self,
        *,
        heartbeat_path: str = DEFAULT_HEARTBEAT_PATH,
        warning_threshold_sec: int = HEARTBEAT_WARNING_SEC,
        critical_threshold_sec: int = HEARTBEAT_CRITICAL_SEC,
        emergency_threshold_sec: int = HEARTBEAT_EMERGENCY_SEC,
        check_interval_sec: int = 60,
        event_bus=None,
        executor=None,
        notifier=None,
        session_factory=None,
    ):
        self.heartbeat_path = Path(heartbeat_path)
        self.warning_threshold = warning_threshold_sec
        self.critical_threshold = critical_threshold_sec
        self.emergency_threshold = emergency_threshold_sec
        self.check_interval = check_interval_sec
        self.event_bus = event_bus
        self.executor = executor
        self.notifier = notifier
        self._session_factory = session_factory

        self._running = False
        self._task: asyncio.Task | None = None
        self._last_alert_level: str = "ok"
        self._emergency_triggered = False

    async def start(self) -> None:
        """Start the dead-man's-switch monitor."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            "[dead-man-switch] Started — warning=%ds, critical=%ds, emergency=%ds",
            self.warning_threshold,
            self.critical_threshold,
            self.emergency_threshold,
        )

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[dead-man-switch] Stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[dead-man-switch] Check failed: %s", e)
            await asyncio.sleep(self.check_interval)

    async def _check_heartbeat(self) -> None:
        """Check heartbeat freshness and take action if stale."""
        staleness = self._get_staleness_sec()

        if staleness is None:
            # No heartbeat file — trading loop may not have started
            if self._last_alert_level != "no_file":
                logger.warning(
                    "[dead-man-switch] No heartbeat file found at %s",
                    self.heartbeat_path,
                )
                self._last_alert_level = "no_file"
            return

        if staleness < self.warning_threshold:
            if self._last_alert_level != "ok":
                logger.info(
                    "[dead-man-switch] Heartbeat recovered — %d sec fresh",
                    staleness,
                )
                self._last_alert_level = "ok"
                self._emergency_triggered = False
            return

        if staleness >= self.emergency_threshold and not self._emergency_triggered:
            # Suppress if no live executor and a backtest is actively running —
            # the heartbeat file is only written by the trading loop, not the backtester.
            if not self.executor and await self._has_active_backtest():
                if self._last_alert_level != "backtest_suppress":
                    logger.info(
                        "[dead-man-switch] Heartbeat stale %ds but backtest is active "
                        "and no live executor — suppressing EMERGENCY.",
                        staleness,
                    )
                    self._last_alert_level = "backtest_suppress"
                return

            logger.critical(
                "[dead-man-switch] EMERGENCY — heartbeat stale %d sec "
                "(threshold=%d). Triggering emergency close.",
                staleness,
                self.emergency_threshold,
            )
            self._last_alert_level = "emergency"
            self._emergency_triggered = True
            await self._trigger_emergency_close()
            return

        if staleness >= self.critical_threshold:
            if self._last_alert_level not in ("critical", "backtest_suppress"):
                # Downgrade to warning if backtest is running with no live executor
                if not self.executor and await self._has_active_backtest():
                    if self._last_alert_level != "backtest_suppress":
                        logger.info(
                            "[dead-man-switch] Heartbeat stale %ds — backtest active, "
                            "no live executor. Suppressing CRITICAL.",
                            staleness,
                        )
                        self._last_alert_level = "backtest_suppress"
                    return

                logger.critical(
                    "[dead-man-switch] CRITICAL — heartbeat stale %d sec "
                    "(threshold=%d). Trading loop may be dead.",
                    staleness,
                    self.critical_threshold,
                )
                self._last_alert_level = "critical"
                await self._send_alert(
                    f"CRITICAL: Trading loop unresponsive for {staleness}s. "
                    f"Emergency close will trigger at {self.emergency_threshold}s."
                )
            return

        if staleness >= self.warning_threshold:
            if self._last_alert_level != "warning":
                logger.warning(
                    "[dead-man-switch] WARNING — heartbeat stale %d sec",
                    staleness,
                )
                self._last_alert_level = "warning"
            return

    async def _has_active_backtest(self) -> bool:
        """Return True if any backtest run is currently pending/running/paused in DB."""
        if not self._session_factory:
            return False
        try:
            async with self._session_factory() as session:
                from alphaloop.db.repositories.backtest_repo import BacktestRepository
                repo = BacktestRepository(session)
                runs = await repo.get_active_runs()
                return len(runs) > 0
        except Exception as e:
            logger.debug("[dead-man-switch] Backtest check failed: %s", e)
            return False

    def _get_staleness_sec(self) -> int | None:
        """Read heartbeat file and return staleness in seconds."""
        if not self.heartbeat_path.exists():
            return None

        try:
            with open(self.heartbeat_path) as f:
                data = json.load(f)

            ts_raw = data.get("timestamp") or data.get("last_heartbeat")
            if not ts_raw:
                return None

            # HeartbeatWriter stores a Unix float; accept ISO strings too
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(str(ts_raw))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            return int((now - ts).total_seconds())
        except (json.JSONDecodeError, ValueError, OSError) as e:
            logger.warning("[dead-man-switch] Failed to read heartbeat: %s", e)
            return None

    async def _trigger_emergency_close(self) -> None:
        """Attempt to close all positions via executor."""
        await self._record_incident(
            "dead_man_switch_triggered",
            "Dead-man switch triggered emergency close",
            severity="critical",
        )
        await self._send_alert(
            "EMERGENCY: Dead-man's-switch activated. "
            "Attempting to close all positions."
        )

        if not self.executor:
            logger.critical(
                "[dead-man-switch] No executor available — CANNOT close positions. "
                "Manual intervention required!"
            )
            return

        try:
            positions = await self.executor.get_open_positions()
            if not positions:
                logger.info("[dead-man-switch] No open positions to close")
                return

            logger.critical(
                "[dead-man-switch] Closing %d open positions", len(positions)
            )
            for pos in positions:
                try:
                    result = await self.executor.close_position(pos.ticket)
                    if result.success:
                        logger.info(
                            "[dead-man-switch] Closed position ticket=%d",
                            pos.ticket,
                        )
                    else:
                        logger.error(
                            "[dead-man-switch] Failed to close ticket=%d: %s",
                            pos.ticket,
                            result.error_message,
                        )
                except Exception as e:
                    logger.error(
                        "[dead-man-switch] Error closing ticket=%d: %s",
                        pos.ticket, e,
                    )
        except Exception as e:
            logger.critical(
                "[dead-man-switch] FATAL — emergency close failed: %s", e
            )

    async def _send_alert(self, message: str) -> None:
        """Send alert via notifier if available."""
        if self.notifier:
            try:
                await self.notifier.send_alert(
                    f"🚨 DEAD-MAN-SWITCH: {message}"
                )
            except Exception as e:
                logger.error("[dead-man-switch] Alert send failed: %s", e)

        # Also publish to event bus
        if self.event_bus:
            try:
                from alphaloop.core.events import RiskLimitHit
                await self.event_bus.publish(
                    RiskLimitHit(
                        symbol=getattr(self.executor, "symbol", ""),
                        limit_type="dead_man_switch",
                        details=message,
                    )
                )
            except Exception:
                pass

    async def _record_incident(
        self,
        incident_type: str,
        details: str,
        *,
        severity: str = "warning",
    ) -> None:
        if not self._session_factory:
            return
        try:
            from alphaloop.supervision.service import SupervisionService

            supervision = SupervisionService(self._session_factory)
            await supervision.record_incident(
                incident_type=incident_type,
                details=details,
                severity=severity,
                symbol=getattr(self.executor, "symbol", None),
                instance_id=None,
                source="dead_man_switch",
                payload=self.status,
            )
        except Exception as e:
            logger.warning("[dead-man-switch] Failed to record incident: %s", e)

    @property
    def status(self) -> dict:
        staleness = self._get_staleness_sec()
        return {
            "running": self._running,
            "heartbeat_staleness_sec": staleness,
            "alert_level": self._last_alert_level,
            "emergency_triggered": self._emergency_triggered,
            "thresholds": {
                "warning": self.warning_threshold,
                "critical": self.critical_threshold,
                "emergency": self.emergency_threshold,
            },
        }
