"""Shared enums and type aliases used across the AlphaLoop system."""

from enum import StrEnum


class TrendDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SetupType(StrEnum):
    PULLBACK = "pullback"
    BREAKOUT = "breakout"
    REVERSAL = "reversal"
    RANGE = "range"
    MOMENTUM = "momentum"
    SCALP = "scalp"


class TradeDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TradeOutcome(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BE"
    OPEN = "OPEN"


class SessionName(StrEnum):
    ASIA_EARLY = "asia_early"
    ASIA_LATE = "asia_late"
    LONDON = "london_session"
    NY = "ny_session"
    LONDON_NY_OVERLAP = "london_ny_overlap"
    WEEKEND = "weekend"


class BacktestState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    DELETED = "deleted"


class EvolutionEventType(StrEnum):
    APPLY = "apply"
    ROLLBACK = "rollback"
    DRIFT_BLOCK = "drift_block"
    OOS_FAIL = "oos_fail"
    PROMOTE = "promote"
    CANARY_START = "canary_start"
    CANARY_END = "canary_end"


class ValidationStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"
    SKIPPED = "skipped"


class StrategyStatus(StrEnum):
    CANDIDATE = "candidate"
    DRY_RUN = "dry_run"
    DEMO = "demo"
    LIVE = "live"
    RETIRED = "retired"


class AIProvider(StrEnum):
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    QWEN = "qwen"
    OLLAMA = "ollama"
