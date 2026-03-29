"""Tests for asset configuration."""

from alphaloop.config.assets import get_asset_config, ASSETS, AssetConfig


def test_known_asset():
    cfg = get_asset_config("XAUUSD")
    assert cfg.symbol == "XAUUSD"
    assert cfg.pip_size == 0.1
    assert cfg.use_dxy_filter is True


def test_broker_suffix_stripped():
    cfg = get_asset_config("XAUUSDm")
    assert cfg.symbol == "XAUUSD"


def test_unknown_asset_falls_back():
    cfg = get_asset_config("ZZZZZZ")
    assert cfg.asset_class == "unknown"
    assert cfg.symbol == "ZZZZZZ"


def test_btc_config():
    cfg = get_asset_config("BTCUSD")
    assert cfg.pip_size == 1.0
    assert cfg.sl_atr_mult == 2.0
    assert cfg.min_session_score == 0.40


def test_all_assets_valid():
    for sym, cfg in ASSETS.items():
        assert isinstance(cfg, AssetConfig)
        assert cfg.pip_size > 0
        assert cfg.sl_min_points > 0
