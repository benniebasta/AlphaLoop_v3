"""
Alert Rules Engine — Configurable alert conditions.

Supports threshold alerts (e.g., daily loss > $500), pattern alerts
(e.g., 3 consecutive losses), and custom conditions. Rules are
evaluated by the event bus and can trigger Telegram notifications.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"


@dataclass
class AlertRule:
    """A configurable alert rule."""
    name: str
    description: str
    severity: AlertSeverity = AlertSeverity.WARNING
    enabled: bool = True
    cooldown_seconds: float = 300.0  # 5 min between repeated alerts
    condition: Callable[[dict], bool] | None = None
    _last_fired: float = field(default=0.0, repr=False)


@dataclass
class Alert:
    """A fired alert instance."""
    rule_name: str
    severity: AlertSeverity
    message: str
    timestamp: float = field(default_factory=time.time)
    status: AlertStatus = AlertStatus.ACTIVE
    data: dict = field(default_factory=dict)


class AlertEngine:
    """Evaluates alert rules and manages fired alerts."""

    def __init__(self, max_alerts: int = 100) -> None:
        self._rules: dict[str, AlertRule] = {}
        self._alerts: list[Alert] = []
        self._max_alerts = max_alerts
        self._callbacks: list[Callable[[Alert], Any]] = []

    def register_rule(self, rule: AlertRule) -> None:
        self._rules[rule.name] = rule
        logger.info("Alert rule registered: %s", rule.name)

    def remove_rule(self, name: str) -> None:
        self._rules.pop(name, None)

    def on_alert(self, callback: Callable[[Alert], Any]) -> None:
        """Register a callback to be called when an alert fires."""
        self._callbacks.append(callback)

    def evaluate(self, context: dict) -> list[Alert]:
        """Evaluate all enabled rules against current context. Returns new alerts."""
        now = time.time()
        new_alerts = []

        for rule in self._rules.values():
            if not rule.enabled or not rule.condition:
                continue
            if now - rule._last_fired < rule.cooldown_seconds:
                continue

            try:
                if rule.condition(context):
                    alert = Alert(
                        rule_name=rule.name,
                        severity=rule.severity,
                        message=f"[{rule.severity.upper()}] {rule.name}: {rule.description}",
                        data=context,
                    )
                    self._alerts.append(alert)
                    if len(self._alerts) > self._max_alerts:
                        self._alerts = self._alerts[-self._max_alerts:]
                    rule._last_fired = now
                    new_alerts.append(alert)

                    for cb in self._callbacks:
                        try:
                            cb(alert)
                        except Exception as e:
                            logger.warning("Alert callback error: %s", e)
            except Exception as e:
                logger.warning("Alert rule '%s' evaluation error: %s", rule.name, e)

        return new_alerts

    def acknowledge(self, index: int) -> bool:
        if 0 <= index < len(self._alerts):
            self._alerts[index].status = AlertStatus.ACKNOWLEDGED
            return True
        return False

    def get_active_alerts(self) -> list[dict]:
        return [
            {
                "rule_name": a.rule_name,
                "severity": a.severity,
                "message": a.message,
                "timestamp": a.timestamp,
                "status": a.status,
            }
            for a in self._alerts
            if a.status == AlertStatus.ACTIVE
        ]

    def get_all_alerts(self, limit: int = 50) -> list[dict]:
        return [
            {
                "rule_name": a.rule_name,
                "severity": a.severity,
                "message": a.message,
                "timestamp": a.timestamp,
                "status": a.status,
            }
            for a in reversed(self._alerts[-limit:])
        ]

    @property
    def rules_summary(self) -> list[dict]:
        return [
            {
                "name": r.name,
                "description": r.description,
                "severity": r.severity,
                "enabled": r.enabled,
                "cooldown_seconds": r.cooldown_seconds,
            }
            for r in self._rules.values()
        ]


# ── Default Rules ────────────────────────────────────────────────────────────

def create_default_rules() -> list[AlertRule]:
    """Create the default set of alert rules."""
    return [
        AlertRule(
            name="daily_loss_limit",
            description="Daily P&L has exceeded loss threshold",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=600,
            condition=lambda ctx: ctx.get("daily_pnl", 0) < ctx.get("daily_loss_threshold", -500),
        ),
        AlertRule(
            name="consecutive_losses",
            description="3+ consecutive losing trades",
            severity=AlertSeverity.WARNING,
            cooldown_seconds=300,
            condition=lambda ctx: ctx.get("consecutive_losses", 0) >= 3,
        ),
        AlertRule(
            name="high_portfolio_heat",
            description="Portfolio heat exceeds safe level",
            severity=AlertSeverity.WARNING,
            cooldown_seconds=300,
            condition=lambda ctx: ctx.get("portfolio_heat_pct", 0) > 5.0,
        ),
        AlertRule(
            name="circuit_breaker_open",
            description="Circuit breaker has opened",
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=600,
            condition=lambda ctx: ctx.get("circuit_breaker_open", False),
        ),
        AlertRule(
            name="spread_spike",
            description="Spread has spiked above normal",
            severity=AlertSeverity.INFO,
            cooldown_seconds=120,
            condition=lambda ctx: ctx.get("spread_spike", False),
        ),
    ]
