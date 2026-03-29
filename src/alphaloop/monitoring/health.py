"""
Health check aggregator — reports status of all system components.
"""

import time
from enum import StrEnum


class ComponentStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class HealthCheck:
    """Aggregates health status from multiple system components."""

    def __init__(self):
        self._checks: dict[str, dict] = {}

    def register(
        self,
        name: str,
        status: ComponentStatus = ComponentStatus.UNKNOWN,
        details: str = "",
    ) -> None:
        self._checks[name] = {
            "status": status,
            "details": details,
            "last_check": time.time(),
        }

    def update(
        self,
        name: str,
        status: ComponentStatus,
        details: str = "",
    ) -> None:
        self._checks[name] = {
            "status": status,
            "details": details,
            "last_check": time.time(),
        }

    @property
    def overall_status(self) -> ComponentStatus:
        if not self._checks:
            return ComponentStatus.UNKNOWN
        statuses = [c["status"] for c in self._checks.values()]
        if any(s == ComponentStatus.UNHEALTHY for s in statuses):
            return ComponentStatus.UNHEALTHY
        if any(s == ComponentStatus.DEGRADED for s in statuses):
            return ComponentStatus.DEGRADED
        if all(s == ComponentStatus.HEALTHY for s in statuses):
            return ComponentStatus.HEALTHY
        return ComponentStatus.UNKNOWN

    def get_report(self) -> dict:
        return {
            "status": self.overall_status,
            "timestamp": time.time(),
            "components": {
                name: {
                    "status": check["status"],
                    "details": check["details"],
                    "last_check": check["last_check"],
                }
                for name, check in self._checks.items()
            },
        }
