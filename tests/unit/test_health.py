"""Tests for health check aggregator."""

from alphaloop.monitoring.health import HealthCheck, ComponentStatus


def test_all_healthy():
    hc = HealthCheck()
    hc.register("db", ComponentStatus.HEALTHY)
    hc.register("mt5", ComponentStatus.HEALTHY)
    assert hc.overall_status == ComponentStatus.HEALTHY


def test_one_degraded():
    hc = HealthCheck()
    hc.register("db", ComponentStatus.HEALTHY)
    hc.register("mt5", ComponentStatus.DEGRADED)
    assert hc.overall_status == ComponentStatus.DEGRADED


def test_one_unhealthy():
    hc = HealthCheck()
    hc.register("db", ComponentStatus.HEALTHY)
    hc.register("mt5", ComponentStatus.UNHEALTHY)
    assert hc.overall_status == ComponentStatus.UNHEALTHY


def test_report_structure():
    hc = HealthCheck()
    hc.register("db", ComponentStatus.HEALTHY, "Connected")
    report = hc.get_report()
    assert "components" in report
    assert "db" in report["components"]
    assert report["components"]["db"]["details"] == "Connected"
