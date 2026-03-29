"""API failure tracking and circuit breaker."""

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """
    Tracks consecutive API failures and opens the circuit
    after a threshold is reached, pausing operations.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        pause_seconds: float = 300.0,
        kill_threshold: int = 10,
    ):
        self.failure_threshold = failure_threshold
        self.pause_seconds = pause_seconds
        self.kill_threshold = kill_threshold
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._circuit_open_until = time.time() + self.pause_seconds
            logger.warning(
                "Circuit breaker OPEN — %d failures, pausing %.0fs",
                self._consecutive_failures,
                self.pause_seconds,
            )

    @property
    def is_open(self) -> bool:
        if self._circuit_open_until > time.time():
            return True
        return False

    @property
    def should_kill(self) -> bool:
        return self._consecutive_failures >= self.kill_threshold

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def status(self) -> dict:
        return {
            "consecutive_failures": self._consecutive_failures,
            "is_open": self.is_open,
            "should_kill": self.should_kill,
            "open_until": self._circuit_open_until,
        }
