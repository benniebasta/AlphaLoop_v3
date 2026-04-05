"""
Pydantic-based configuration with layered resolution.

Priority chain:
  1. Runtime overrides (via ConfigChanged events)
  2. Database (app_settings table, set via WebUI)
  3. Environment variables / .env file
  4. Hardcoded defaults in this file

At startup, .env is loaded to bootstrap the DB connection.
Once the DB is available, SettingsService merges DB values on top.
"""

from __future__ import annotations

import logging
from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings

from alphaloop.core.constants import RISK_HARD_CAPS

logger = logging.getLogger(__name__)


class BrokerConfig(BaseModel):
    server: str = "MetaQuotes-Demo"
    login: int = 0
    password: SecretStr = SecretStr("")
    symbol: str = "XAUUSDm"
    terminal_path: str = ""
    magic: int = 20240101
    deviation: int = 20


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = 0.01       # 1%
    risk_per_trade_min: float = 0.005      # floor 0.5%
    max_daily_loss_pct: float = 0.03       # 3%
    max_concurrent_trades: int = 2
    consecutive_loss_limit: int = 5
    commission_per_lot_usd: float = 7.0
    leverage: int = 100
    contract_size: float = 100.0
    max_session_loss_pct: float = 0.01
    max_portfolio_heat_pct: float = 0.03
    sl_slippage_buffer: float = 1.15
    margin_cap_pct: float = 0.20
    risk_score_threshold: float = 0.85
    macro_modifier_abort_threshold: float = 0.25

    @field_validator(
        "risk_per_trade_pct",
        "max_daily_loss_pct",
        "margin_cap_pct",
        "max_portfolio_heat_pct",
        "risk_score_threshold",
        "max_session_loss_pct",
        "consecutive_loss_limit",
        mode="after",
    )
    @classmethod
    def enforce_hard_caps(cls, v: float, info) -> float:
        caps = RISK_HARD_CAPS.get(info.field_name)
        if caps:
            lo, hi, default = caps
            if v < lo or v > hi:
                clamped = max(lo, min(hi, v))
                logger.critical(
                    f"[RiskConfig] HARD CAP: {info.field_name}={v} outside "
                    f"[{lo}, {hi}] — clamping to {clamped}"
                )
                return clamped
        return v


class SignalConfig(BaseModel):
    trading_mode: str = "swing"
    min_confidence: float = 0.70
    min_rr_ratio: float = 1.5
    max_volatility_atr_pct: float = 2.5
    avoid_setups: list[str] = ["breakout_chase"]
    check_h1_trend: bool = True
    check_rsi: bool = True
    rsi_ob: float = 70.0
    rsi_os: float = 30.0
    check_news: bool = True
    check_setup: bool = True
    min_volatility_atr_pct: float = 0.05
    check_tick_jump: bool = True
    tick_jump_atr_max: float = 0.8
    check_liq_vacuum: bool = True
    liq_vacuum_spike_mult: float = 2.5
    liq_vacuum_body_pct: float = 30.0
    trade_cooldown_minutes: int = 15
    max_slippage_atr: float = 0.3
    max_signal_age_seconds: int = 90
    check_fvg: bool = True
    fvg_min_size_atr: float = 0.15
    fvg_lookback: int = 20
    use_bos_guard: bool = True
    bos_min_break_atr: float = 0.2
    bos_swing_lookback: int = 20
    use_vwap_guard: bool = True
    vwap_extension_max_atr: float = 1.5
    use_correlation_guard: bool = True
    correlation_threshold_block: float = 0.90
    correlation_threshold_reduce: float = 0.75
    circuit_pause_sec: int = 300
    circuit_kill_count: int = 10
    pipeline_size_floor: float = 0.20
    pipeline_version: str = "v4"           # "v4" = institutional 8-stage pipeline


class SessionConfig(BaseModel):
    london_open: str = "07:00"
    london_close: str = "16:00"
    ny_open: str = "13:00"
    ny_close: str = "21:00"
    avoid_pre_news_minutes: int = 30
    avoid_post_news_minutes: int = 15
    min_spread_points: float = 30.0


class APIConfig(BaseModel):
    signal_provider: str = "gemini"
    signal_model: str = "gemini-2.5-flash-lite"
    gemini_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    deepseek_api_key: SecretStr = SecretStr("")
    xai_api_key: SecretStr = SecretStr("")
    claude_api_key: SecretStr = SecretStr("")
    claude_model: str = "claude-sonnet-4-6"
    claude_enabled: bool = True
    qwen_api_key: SecretStr = SecretStr("")
    qwen_api_base: str = "https://api.together.ai/v1"
    qwen_local_base: str = "http://localhost:11434/v1"
    qwen_signal_model: str = "Qwen/Qwen2.5-7B-Instruct-Turbo"
    qwen_validator_model: str = "Qwen/Qwen2.5-32B-Instruct"
    qwen_local_signal_model: str = "qwen2.5:7b"
    qwen_local_validator_model: str = "qwen2.5:32b"
    qwen_validator_timeout: int = 25
    qwen_validator_enabled: bool = False
    polymarket_api_url: str = "https://clob.polymarket.com"
    news_api_key: SecretStr = SecretStr("")


class EvolutionConfig(BaseModel):
    min_trades_for_tuning: int = 30
    max_param_change_pct: float = 0.15
    max_total_drift_pct: float = 0.40
    rollback_wr_drop: float = 0.10
    rollback_expectancy_drop: float = 0.15
    drift_block_threshold: float = 0.15
    oos_min_wr: float = 0.40
    confidence_gate: float = 0.65
    promote_min_cycles: int = 5
    promote_min_drift_pct: float = 0.25
    canary_lot_fraction: float = 0.10


class DBConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///alphaloop.db"
    pool_size: int = 5
    echo: bool = False


class TelegramConfig(BaseModel):
    token: SecretStr = SecretStr("")
    chat_id: str = ""
    enabled: bool = True


class AppConfig(BaseSettings):
    """
    Root configuration. Loaded from environment / .env at startup.
    DB overrides are merged later by SettingsService.
    """

    broker: BrokerConfig = BrokerConfig()
    risk: RiskConfig = RiskConfig()
    signal: SignalConfig = SignalConfig()
    session: SessionConfig = SessionConfig()
    api: APIConfig = APIConfig()
    db: DBConfig = DBConfig()
    telegram: TelegramConfig = TelegramConfig()
    evolution: EvolutionConfig = EvolutionConfig()

    auth_token: str = ""
    dry_run: bool = True
    log_level: str = "INFO"
    environment: str = "dev"

    model_config = {
        "env_prefix": "",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }
