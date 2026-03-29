"""Extended tests for CircuitBreaker recovery and state transitions."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from alphaloop.trading.circuit_breaker import CircuitBreaker


# ── Opening the circuit ──────────────────────────────────────────────────────


def test_opens_after_failures():
    """Circuit opens once failure_threshold is reached."""
    cb = CircuitBreaker(failure_threshold=3, pause_seconds=60.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open is True


def test_stays_closed_below_threshold():
    """Circuit stays closed when failures < threshold."""
    cb = CircuitBreaker(failure_threshold=5, pause_seconds=60.0)
    for _ in range(4):
        cb.record_failure()
    # 4 < 5, but is_open depends on _circuit_open_until being set
    # After 4 failures (< 5), the timer should NOT have been set
    assert cb._circuit_open_until == 0.0


# ── Success resets ───────────────────────────────────────────────────────────


def test_success_resets_failure_count():
    """record_success() resets _consecutive_failures to 0."""
    cb = CircuitBreaker(failure_threshold=5, pause_seconds=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb._consecutive_failures == 2
    cb.record_success()
    assert cb._consecutive_failures == 0


def test_success_after_open_resets():
    """A success after the circuit was opened resets failure count."""
    cb = CircuitBreaker(failure_threshold=2, pause_seconds=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb._consecutive_failures == 2
    cb.record_success()
    assert cb._consecutive_failures == 0


# ── Kill threshold ───────────────────────────────────────────────────────────


def test_should_kill_false_below_threshold():
    """should_kill is False when failures < kill_threshold."""
    cb = CircuitBreaker(failure_threshold=3, pause_seconds=60.0, kill_threshold=10)
    for _ in range(9):
        cb.record_failure()
    assert cb.should_kill is False


def test_should_kill_true_at_threshold():
    """should_kill is True once kill_threshold is reached."""
    cb = CircuitBreaker(failure_threshold=3, pause_seconds=60.0, kill_threshold=10)
    for _ in range(10):
        cb.record_failure()
    assert cb.should_kill is True


def test_should_kill_resets_on_success():
    """should_kill goes back to False after a success resets the count."""
    cb = CircuitBreaker(failure_threshold=3, pause_seconds=60.0, kill_threshold=5)
    for _ in range(5):
        cb.record_failure()
    assert cb.should_kill is True
    cb.record_success()
    assert cb.should_kill is False


# ── Pause time recovery ──────────────────────────────────────────────────────


def test_circuit_closes_after_pause():
    """After pause_seconds elapse, is_open returns False."""
    cb = CircuitBreaker(failure_threshold=2, pause_seconds=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open is True

    # Fast-forward time past the pause window
    with patch("time.time", return_value=time.time() + 2.0):
        assert cb.is_open is False


def test_full_recovery_after_pause_and_success():
    """After pause time + success, circuit is fully closed and count is 0."""
    cb = CircuitBreaker(failure_threshold=2, pause_seconds=1.0)
    cb.record_failure()
    cb.record_failure()

    # Fast-forward past the pause window
    future = time.time() + 2.0
    with patch("time.time", return_value=future):
        assert cb.is_open is False
        # Record success while time is still in the future
        cb.record_success()
        assert cb._consecutive_failures == 0
        assert cb.is_open is False


# ── Status dict ──────────────────────────────────────────────────────────────


def test_status_dict():
    """status property returns expected keys."""
    cb = CircuitBreaker(failure_threshold=3, pause_seconds=60.0)
    s = cb.status
    assert "consecutive_failures" in s
    assert "is_open" in s
    assert "should_kill" in s
    assert "open_until" in s
