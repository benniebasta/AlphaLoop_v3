"""Tests for core configuration."""

from alphaloop.core.config import AppConfig, RiskConfig


def test_default_config():
    cfg = AppConfig()
    assert cfg.dry_run is True
    assert cfg.environment == "dev"
    assert cfg.risk.risk_per_trade_pct == 0.01
    assert cfg.broker.magic == 20240101


def test_risk_hard_caps_clamp_high():
    risk = RiskConfig(risk_per_trade_pct=0.99)
    assert risk.risk_per_trade_pct == 0.05  # clamped to max


def test_risk_hard_caps_clamp_low():
    risk = RiskConfig(risk_per_trade_pct=0.0001)
    assert risk.risk_per_trade_pct == 0.001  # clamped to min


def test_risk_within_caps():
    risk = RiskConfig(risk_per_trade_pct=0.02)
    assert risk.risk_per_trade_pct == 0.02  # unchanged
