"""Tests for CircuitBreaker."""

from alphaloop.trading.circuit_breaker import CircuitBreaker


def test_circuit_closed_initially():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.is_open is False
    assert cb.should_kill is False


def test_circuit_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, pause_seconds=300)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open is False
    cb.record_failure()
    assert cb.is_open is True


def test_success_resets_counter():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    assert cb.consecutive_failures == 0
    cb.record_failure()
    assert cb.is_open is False


def test_kill_threshold():
    cb = CircuitBreaker(failure_threshold=3, kill_threshold=5)
    for _ in range(5):
        cb.record_failure()
    assert cb.should_kill is True
