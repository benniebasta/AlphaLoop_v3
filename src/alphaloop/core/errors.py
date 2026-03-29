"""Custom exception hierarchy for AlphaLoop."""


class AlphaLoopError(Exception):
    """Base exception for all AlphaLoop errors."""


class ConfigError(AlphaLoopError):
    """Invalid or missing configuration."""


class SignalError(AlphaLoopError):
    """Signal generation or parsing failure."""


class ValidationError(AlphaLoopError):
    """Signal validation failure."""


class ExecutionError(AlphaLoopError):
    """Trade execution failure (MT5 or broker-side)."""


class RiskLimitError(AlphaLoopError):
    """Risk limit exceeded — trade blocked by safety guards."""


class RateLimitError(AlphaLoopError):
    """AI provider rate limit exceeded."""


class CircuitBreakerError(AlphaLoopError):
    """Circuit breaker activated — trading paused."""


class DatabaseError(AlphaLoopError):
    """Database connection or query failure."""
