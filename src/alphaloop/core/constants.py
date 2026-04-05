"""All magic numbers and system-wide constants, documented and centralised."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRATEGY_VERSIONS_DIR = PROJECT_ROOT / "strategy_versions"

# Trading loop
POLL_INTERVAL_SEC = 300
POST_SESSION_HOUR_UTC = 22

# Circuit breaker
CIRCUIT_PAUSE_THRESHOLD = 5
CIRCUIT_PAUSE_SEC_DEFAULT = 300
CIRCUIT_KILL_COUNT_DEFAULT = 5  # Must match SETTING_DEFAULTS["CIRCUIT_KILL_COUNT"] in settings_service.py

# Risk defaults
DEFAULT_RISK_PCT = 0.01
MAX_DAILY_LOSS_PCT = 0.03
MAX_CONCURRENT_TRADES = 2
CONSECUTIVE_LOSS_LIMIT = 5
SL_SLIPPAGE_BUFFER = 1.15
SL_SLIPPAGE_BUFFER_CRYPTO = 1.30
MARGIN_CAP_PCT = 0.20
RISK_SCORE_THRESHOLD = 0.85
MACRO_ABORT_THRESHOLD = 0.25
MAX_SESSION_LOSS_PCT = 0.01
MAX_PORTFOLIO_HEAT_PCT = 0.03

# Risk hard caps
RISK_HARD_CAPS: dict[str, tuple[float, float, float]] = {
    "risk_per_trade_pct": (0.001, 0.05, 0.01),
    "max_daily_loss_pct": (0.005, 0.10, 0.03),
    "margin_cap_pct": (0.05, 0.50, 0.20),
    "max_portfolio_heat_pct": (0.01, 0.10, 0.03),
    "risk_score_threshold": (0.5, 1.0, 0.85),
    "max_session_loss_pct": (0.0, 0.15, 0.10),
    "consecutive_loss_limit": (1, 20, 5),
}

# Signal defaults
MIN_CONFIDENCE_DEFAULT = 0.55  # Must match SETTING_DEFAULTS["MIN_CONFIDENCE"] in settings_service.py
MIN_RR_RATIO_DEFAULT = 1.5
MAX_VOLATILITY_ATR_PCT = 2.5

# Commission
COMMISSION_PER_LOT_USD = 7.0
DEFAULT_LEVERAGE = 100
DEFAULT_CONTRACT_SIZE = 100.0

# MT5
MT5_MAGIC_NUMBER = 20240101
MT5_MAX_DEVIATION = 20

# Rate limiting
AI_RATE_LIMIT_PER_MIN = 5
AI_RATE_LIMIT_WINDOW_SEC = 60.0

# Monitoring
METRICS_BUCKET_SIZE_SEC = 300
METRICS_MAX_BUCKETS = 288

# WebUI
WEBUI_DEFAULT_PORT = 8090
WEBUI_RATE_LIMIT_GET = 200
WEBUI_RATE_LIMIT_POST = 30

# Evolution / AutoLearn
EVO_MIN_TRADES = 30
EVO_MAX_PARAM_CHANGE_PCT = 0.15
EVO_MAX_DRIFT_PCT = 0.40

# Deployment pipeline thresholds
DEPLOY_CANDIDATE_MIN_SHARPE = 1.5
DEPLOY_CANDIDATE_MIN_TRADES = 50
DEPLOY_DRY_RUN_MIN_DAYS = 7
DEPLOY_DEMO_MIN_DAYS = 30
DEPLOY_RETIRE_SHARPE_THRESHOLD = 0.5
