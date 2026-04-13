"""
B3: Comprehensive tests for resolve_construction_params() and TF tools_config.

Tests the full 5-layer precedence chain, sanity clamps, timeframe normalization,
cross-symbol/TF leakage prevention, and live/backtest parity.

Risk context: These parameters control SL/TP placement — incorrect resolution
can cause oversized SL (blowing risk limits) or undersized SL (stop hunted).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from alphaloop.config.assets import AssetConfig, get_asset_config, _tf, ASSETS
from alphaloop.config.asset_classes import merge_tools_config, get_asset_class_defaults
from alphaloop.trading.strategy_loader import resolve_construction_params, _CONSTRUCTION_KEYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _xau() -> AssetConfig:
    """Return the real XAUUSD config (has full TF calibration)."""
    return get_asset_config("XAUUSD")


def _minimal_asset(**overrides) -> AssetConfig:
    """Bare-bones asset with no TF calibration for isolation tests."""
    defaults = dict(
        symbol="TEST",
        display_name="Test",
        asset_class="forex_major",
        mt5_symbol="TEST",
        pip_size=0.0001,
        sl_atr_mult=1.5,
        tp1_rr=1.5,
        tp2_rr=2.5,
        sl_min_points=100.0,
        sl_max_points=300.0,
        entry_zone_atr_mult=0.25,
        default_params_by_timeframe={},
    )
    defaults.update(overrides)
    return AssetConfig(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Five-layer precedence chain
# ═══════════════════════════════════════════════════════════════════════════════

class TestFiveLayerPrecedence:
    """Each layer must beat the one below it, never the one above."""

    def test_layer0_asset_base_only(self):
        """With no overrides, returns AssetConfig base values."""
        asset = _minimal_asset(sl_min_points=42.0, sl_max_points=420.0)
        result = resolve_construction_params({}, "M15", asset)
        assert result["sl_min_points"] == 42.0
        assert result["sl_max_points"] == 420.0
        assert result["sl_atr_mult"] == 1.5
        assert result["tp1_rr"] == 1.5
        assert result["tp2_rr"] == 2.5
        assert result["entry_zone_atr_mult"] == 0.25
        assert result["sl_buffer_atr"] == 0.15  # hardcoded default

    def test_layer1_tf_calibration_overrides_base(self):
        """TF calibration (layer 1) overrides base (layer 0)."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            sl_atr_mult=1.5,
            default_params_by_timeframe={
                "M1": {"sl_min_points": 10.0, "sl_atr_mult": 0.8},
            },
        )
        result = resolve_construction_params({}, "M1", asset)
        assert result["sl_min_points"] == 10.0  # layer 1 wins
        assert result["sl_atr_mult"] == 0.8     # layer 1 wins
        assert result["tp1_rr"] == 1.5           # not in layer 1 → falls to layer 0

    def test_layer2_db_overrides_tf_calibration(self):
        """DB user overrides (layer 2) override TF calibration (layer 1)."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            default_params_by_timeframe={
                "M15": {"sl_min_points": 150.0, "sl_atr_mult": 1.5},
            },
        )
        db_overrides = {
            "M15": {"sl_min_points": 200.0},  # user bumped sl_min
        }
        result = resolve_construction_params({}, "M15", asset, tf_db_overrides=db_overrides)
        assert result["sl_min_points"] == 200.0  # layer 2 wins over layer 1's 150
        assert result["sl_atr_mult"] == 1.5       # layer 1 still active (not overridden by layer 2)

    def test_layer3_strategy_params_override_db(self):
        """Strategy flat params (layer 3) override DB overrides (layer 2)."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            default_params_by_timeframe={"M15": {"sl_min_points": 150.0}},
        )
        db_overrides = {"M15": {"sl_min_points": 200.0}}
        strategy = {"params": {"sl_min_points": 250.0}}

        result = resolve_construction_params(
            strategy, "M15", asset, tf_db_overrides=db_overrides,
        )
        assert result["sl_min_points"] == 250.0  # layer 3 wins

    def test_layer4_strategy_params_by_tf_highest_priority(self):
        """Strategy per-TF params (layer 4) override everything."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            default_params_by_timeframe={"M15": {"sl_min_points": 150.0}},
        )
        db_overrides = {"M15": {"sl_min_points": 200.0}}
        strategy = {
            "params": {"sl_min_points": 250.0},
            "params_by_timeframe": {"M15": {"sl_min_points": 300.0}},
        }

        result = resolve_construction_params(
            strategy, "M15", asset, tf_db_overrides=db_overrides,
        )
        assert result["sl_min_points"] == 300.0  # layer 4 wins over everything

    def test_partial_overrides_merge_not_replace(self):
        """Higher layers only override keys they specify; others fall through."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            sl_max_points=300.0,
            tp1_rr=1.5,
            tp2_rr=2.5,
            default_params_by_timeframe={
                "H1": {"sl_min_points": 200.0, "sl_atr_mult": 2.0},
            },
        )
        strategy = {"params": {"tp1_rr": 2.0}}  # only override tp1_rr

        result = resolve_construction_params(strategy, "H1", asset)
        assert result["sl_min_points"] == 200.0       # from layer 1
        assert result["sl_atr_mult"] == 2.0            # from layer 1
        assert result["tp1_rr"] == 2.0                  # from layer 3
        assert result["tp2_rr"] == 2.5                  # from layer 0 (untouched)
        assert result["entry_zone_atr_mult"] == 0.25   # from layer 0

    def test_all_seven_keys_are_present(self):
        """Returned dict always contains all 7 construction keys."""
        result = resolve_construction_params({}, "M15", _minimal_asset())
        for key in _CONSTRUCTION_KEYS:
            assert key in result, f"Missing key: {key}"
        assert len(result) == len(_CONSTRUCTION_KEYS)

    def test_real_xauusd_m1_vs_d1_diverges(self):
        """Real XAUUSD M1 and D1 params must differ substantially."""
        xau = _xau()
        m1 = resolve_construction_params({}, "M1", xau)
        d1 = resolve_construction_params({}, "D1", xau)

        assert m1["sl_min_points"] < d1["sl_min_points"]
        assert m1["sl_max_points"] < d1["sl_max_points"]
        assert m1["sl_atr_mult"] < d1["sl_atr_mult"]
        assert m1["tp1_rr"] < d1["tp1_rr"]
        # D1 should be at least 10x wider on sl_min
        assert d1["sl_min_points"] / m1["sl_min_points"] >= 10

    def test_strategy_as_object_with_attrs(self):
        """Strategy can be an object with attributes, not just a dict."""
        asset = _minimal_asset(sl_atr_mult=1.5)
        strat = SimpleNamespace(params={"sl_atr_mult": 2.5}, params_by_timeframe=None)
        result = resolve_construction_params(strat, "M15", asset)
        assert result["sl_atr_mult"] == 2.5


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Timeframe normalization
# ═══════════════════════════════════════════════════════════════════════════════

class TestTimeframeNormalization:

    def test_lowercase_tf_is_uppercased(self):
        """'m15' should match 'M15' calibration."""
        asset = _minimal_asset(
            default_params_by_timeframe={"M15": {"sl_min_points": 999.0}},
        )
        result = resolve_construction_params({}, "m15", asset)
        assert result["sl_min_points"] == 999.0

    def test_mixed_case_tf(self):
        asset = _minimal_asset(
            default_params_by_timeframe={"H4": {"tp1_rr": 9.0}},
        )
        result = resolve_construction_params({}, "h4", asset)
        assert result["tp1_rr"] == 9.0

    def test_unknown_tf_falls_to_base(self):
        """An unknown TF like 'W1' should return base config (no crash)."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            default_params_by_timeframe={"M15": {"sl_min_points": 150.0}},
        )
        result = resolve_construction_params({}, "W1", asset)
        assert result["sl_min_points"] == 100.0  # layer 0 only

    def test_db_overrides_also_normalize_tf(self):
        """DB overrides keyed as 'M15' should be found when queried as 'm15'."""
        asset = _minimal_asset()
        db_overrides = {"M15": {"sl_min_points": 777.0}}
        # Query with lowercase
        result = resolve_construction_params({}, "m15", asset, tf_db_overrides=db_overrides)
        assert result["sl_min_points"] == 777.0

    def test_layer4_params_by_timeframe_normalizes(self):
        """strategy.params_by_timeframe keyed as 'M15' found when queried as 'm15'."""
        asset = _minimal_asset()
        strategy = {"params_by_timeframe": {"M15": {"tp2_rr": 5.0}}}
        result = resolve_construction_params(strategy, "m15", asset)
        assert result["tp2_rr"] == 5.0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Sanity clamps and bad/malformed values
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanityClamps:

    def test_negative_sl_min_clamped_to_1(self):
        result = resolve_construction_params(
            {"params": {"sl_min_points": -10.0}}, "M15", _minimal_asset(),
        )
        assert result["sl_min_points"] == 1.0

    def test_zero_sl_min_clamped_to_1(self):
        result = resolve_construction_params(
            {"params": {"sl_min_points": 0.0}}, "M15", _minimal_asset(),
        )
        assert result["sl_min_points"] == 1.0

    def test_sl_max_lte_sl_min_auto_expands(self):
        result = resolve_construction_params(
            {"params": {"sl_min_points": 100.0, "sl_max_points": 50.0}},
            "M15", _minimal_asset(),
        )
        assert result["sl_max_points"] == 100.0 * 10  # auto-expands to 10x

    def test_sl_max_equals_sl_min_also_expands(self):
        result = resolve_construction_params(
            {"params": {"sl_min_points": 100.0, "sl_max_points": 100.0}},
            "M15", _minimal_asset(),
        )
        assert result["sl_max_points"] == 1000.0

    def test_negative_tp1_rr_clamped_to_0_5(self):
        result = resolve_construction_params(
            {"params": {"tp1_rr": -1.0}}, "M15", _minimal_asset(),
        )
        assert result["tp1_rr"] == 0.5

    def test_zero_tp1_rr_clamped_to_0_5(self):
        result = resolve_construction_params(
            {"params": {"tp1_rr": 0.0}}, "M15", _minimal_asset(),
        )
        assert result["tp1_rr"] == 0.5

    def test_tp2_rr_less_than_tp1_rr_clamped(self):
        """tp2 < tp1 is illogical — tp2 gets clamped to tp1."""
        result = resolve_construction_params(
            {"params": {"tp1_rr": 2.0, "tp2_rr": 1.0}}, "M15", _minimal_asset(),
        )
        assert result["tp2_rr"] == 2.0  # clamped up to tp1

    def test_negative_sl_atr_mult_clamped_to_0(self):
        result = resolve_construction_params(
            {"params": {"sl_atr_mult": -0.5}}, "M15", _minimal_asset(),
        )
        assert result["sl_atr_mult"] == 0.0

    def test_string_value_in_params_coerced_to_float(self):
        """Params from JSON deserialization might arrive as strings."""
        result = resolve_construction_params(
            {"params": {"sl_min_points": "200.0"}}, "M15", _minimal_asset(),
        )
        assert result["sl_min_points"] == 200.0
        assert isinstance(result["sl_min_points"], float)

    def test_none_strategy_is_safe(self):
        """None or empty strategy dict must not crash."""
        result = resolve_construction_params(None, "M15", _minimal_asset())
        assert result["sl_min_points"] == 100.0  # layer 0

    def test_empty_dict_strategy(self):
        result = resolve_construction_params({}, "M15", _minimal_asset())
        assert result["sl_min_points"] == 100.0

    def test_strategy_with_none_params(self):
        result = resolve_construction_params({"params": None}, "M15", _minimal_asset())
        assert result["sl_min_points"] == 100.0

    def test_all_values_are_float(self):
        """Every returned value must be a float — never int, str, or None."""
        result = resolve_construction_params({}, "M15", _xau())
        for key, value in result.items():
            assert isinstance(value, float), f"{key} is {type(value)}, expected float"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. No symbol/TF leakage — cross-contamination prevention
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoLeakage:

    def test_different_symbols_get_different_results(self):
        """XAUUSD and EURUSD on the same TF must differ (different pip/SL scales)."""
        xau = resolve_construction_params({}, "M15", get_asset_config("XAUUSD"))
        eur = resolve_construction_params({}, "M15", get_asset_config("EURUSD"))
        assert xau["sl_min_points"] != eur["sl_min_points"]
        # Gold SL should be much larger than EUR SL in points
        assert xau["sl_min_points"] > eur["sl_min_points"]

    def test_different_tfs_same_symbol_diverge(self):
        """M1 vs H4 on same asset must produce different params."""
        xau = _xau()
        m1 = resolve_construction_params({}, "M1", xau)
        h4 = resolve_construction_params({}, "H4", xau)
        assert m1["sl_min_points"] != h4["sl_min_points"]
        assert m1["sl_atr_mult"] != h4["sl_atr_mult"]

    def test_db_overrides_dont_leak_across_tf(self):
        """DB override on M15 must NOT affect H1 resolution."""
        asset = _minimal_asset(
            sl_min_points=100.0,
            default_params_by_timeframe={
                "M15": {"sl_min_points": 150.0},
                "H1": {"sl_min_points": 250.0},
            },
        )
        db_overrides = {"M15": {"sl_min_points": 999.0}}  # only on M15

        m15 = resolve_construction_params({}, "M15", asset, tf_db_overrides=db_overrides)
        h1 = resolve_construction_params({}, "H1", asset, tf_db_overrides=db_overrides)

        assert m15["sl_min_points"] == 999.0  # DB override applied
        assert h1["sl_min_points"] == 250.0    # H1 unaffected

    def test_layer4_per_tf_doesnt_leak(self):
        """strategy.params_by_timeframe[M5] must NOT affect M15."""
        asset = _minimal_asset(sl_atr_mult=1.5)
        strategy = {
            "params_by_timeframe": {"M5": {"sl_atr_mult": 9.0}},
        }
        m15 = resolve_construction_params(strategy, "M15", asset)
        m5 = resolve_construction_params(strategy, "M5", asset)
        assert m15["sl_atr_mult"] == 1.5  # unaffected by M5
        assert m5["sl_atr_mult"] == 9.0   # layer 4 applied

    def test_resolve_is_pure_no_mutation(self):
        """Calling resolve must NOT mutate the input strategy or asset."""
        asset = _minimal_asset(sl_min_points=100.0)
        strategy = {"params": {"sl_min_points": 200.0}}
        strategy_copy = {"params": {"sl_min_points": 200.0}}

        resolve_construction_params(strategy, "M15", asset)

        assert strategy == strategy_copy  # unchanged
        assert asset.sl_min_points == 100.0  # unchanged


# ═══════════════════════════════════════════════════════════════════════════════
# 5. All 10 assets have TF calibration with monotonic scaling
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllAssetsTFCalibration:

    @pytest.mark.parametrize("symbol", list(ASSETS.keys()))
    def test_asset_has_tf_calibration(self, symbol):
        """Every asset must have default_params_by_timeframe with at least M1-D1."""
        asset = get_asset_config(symbol)
        tf_map = asset.default_params_by_timeframe
        assert tf_map, f"{symbol} has no TF calibration"
        for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
            assert tf in tf_map, f"{symbol} missing TF calibration for {tf}"

    @pytest.mark.parametrize("symbol", list(ASSETS.keys()))
    def test_sl_min_monotonically_increases(self, symbol):
        """sl_min_points should increase as timeframe widens (M1 < M5 < ... < D1)."""
        asset = get_asset_config(symbol)
        tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        values = [
            resolve_construction_params({}, tf, asset)["sl_min_points"]
            for tf in tfs
        ]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1], (
                f"{symbol} sl_min_points not monotonic: "
                f"{tfs[i-1]}={values[i-1]} > {tfs[i]}={values[i]}"
            )

    @pytest.mark.parametrize("symbol", list(ASSETS.keys()))
    def test_sl_atr_mult_monotonically_increases(self, symbol):
        """sl_atr_mult should increase as timeframe widens."""
        asset = get_asset_config(symbol)
        tfs = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        values = [
            resolve_construction_params({}, tf, asset)["sl_atr_mult"]
            for tf in tfs
        ]
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1], (
                f"{symbol} sl_atr_mult not monotonic: "
                f"{tfs[i-1]}={values[i-1]} > {tfs[i]}={values[i]}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TF tools_config merge order
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolsConfigMerge:

    def test_asset_class_defaults_are_base(self):
        """merge_tools_config with empty strategy returns class defaults."""
        merged = merge_tools_config("crypto", {})
        assert "volatility_filter" in merged
        assert merged["volatility_filter"]["max_atr_pct"] == 5.0  # crypto default

    def test_strategy_overrides_class_defaults(self):
        """Strategy-level tools_config overrides asset-class defaults."""
        merged = merge_tools_config("crypto", {
            "volatility_filter": {"max_atr_pct": 8.0},
        })
        assert merged["volatility_filter"]["max_atr_pct"] == 8.0  # strategy wins
        # min_atr_pct should still come from class defaults
        assert merged["volatility_filter"]["min_atr_pct"] == 0.02

    def test_strategy_adds_new_plugin(self):
        """Strategy can introduce a plugin not in class defaults."""
        merged = merge_tools_config("crypto", {
            "custom_plugin": {"threshold": 0.5},
        })
        assert "custom_plugin" in merged
        assert merged["custom_plugin"]["threshold"] == 0.5

    def test_unknown_asset_class_returns_only_strategy(self):
        """Unknown asset class has no defaults — only strategy config returned."""
        merged = merge_tools_config("exotic_unknown", {
            "my_tool": {"x": 1},
        })
        assert merged == {"my_tool": {"x": 1}}

    def test_tf_calibration_tools_config_structure(self):
        """_tf() helper creates proper tools_config entries."""
        d = _tf(100, 500, 0.15, 2.5, 0.25, 1.5, 1.5, max_atr_pct=0.03, min_atr_pct=0.003)
        assert "tools_config" in d
        assert "volatility_filter" in d["tools_config"]
        assert d["tools_config"]["volatility_filter"]["max_atr_pct"] == 0.03
        assert d["tools_config"]["volatility_filter"]["min_atr_pct"] == 0.003

    def test_tf_calibration_no_tools_config_when_no_tool_params(self):
        """_tf() with no tool params should NOT create a tools_config key."""
        d = _tf(100, 500, 0.15, 2.5, 0.25, 1.5, 1.5)
        assert "tools_config" not in d

    def test_tf_helper_all_tool_params(self):
        """_tf() creates entries for all supported tool params."""
        d = _tf(
            100, 500, 0.15, 2.5, 0.25, 1.5, 1.5,
            max_atr_pct=0.03, min_atr_pct=0.003,
            adx_thresh=25.0, fvg_min_atr=0.2,
            liq_spike=3.0, tick_atr=0.8, vwap_band=1.5,
        )
        tc = d["tools_config"]
        assert tc["volatility_filter"]["max_atr_pct"] == 0.03
        assert tc["volatility_filter"]["min_atr_pct"] == 0.003
        assert tc["adx_filter"]["adx_threshold"] == 25.0
        assert tc["fvg_guard"]["fvg_min_atr"] == 0.2
        assert tc["liq_vacuum_guard"]["spike_mult"] == 3.0
        assert tc["tick_jump_guard"]["tick_jump_atr_max"] == 0.8
        assert tc["vwap_guard"]["vwap_band_atr"] == 1.5

    def test_real_xauusd_m15_has_tools_config(self):
        """XAUUSD M15 TF calibration includes volatility_filter tools_config."""
        xau = _xau()
        m15_cfg = xau.default_params_by_timeframe.get("M15", {})
        tc = m15_cfg.get("tools_config", {})
        assert "volatility_filter" in tc
        assert tc["volatility_filter"]["max_atr_pct"] == 0.03


# ═══════════════════════════════════════════════════════════════════════════════
# 7. BaseTool.configure() plumbing
# ═══════════════════════════════════════════════════════════════════════════════

class TestBaseToolConfigure:

    def test_configure_sets_config_dict(self):
        """BaseTool.configure() stores params that subclass can read."""
        from alphaloop.tools.base import BaseTool, ToolResult

        class DummyTool(BaseTool):
            name = "dummy"
            async def run(self, context):
                return ToolResult(passed=True, reason="ok")

        tool = DummyTool()
        assert tool.config == {}
        tool.configure({"max_atr_pct": 0.05, "min_atr_pct": 0.001})
        assert tool.config["max_atr_pct"] == 0.05
        assert tool.config["min_atr_pct"] == 0.001

    def test_volatility_filter_reads_config(self):
        """VolatilityFilter should read thresholds from self.config."""
        from alphaloop.tools.plugins.volatility_filter.tool import VolatilityFilter
        vf = VolatilityFilter()
        vf.configure({"max_atr_pct": 0.05, "min_atr_pct": 0.001})
        # We can't run the tool without a full context, but we can verify
        # the config was stored properly
        assert vf.config["max_atr_pct"] == 0.05
        assert vf.config["min_atr_pct"] == 0.001

    def test_configure_replaces_previous_config(self):
        """Calling configure twice replaces, not merges."""
        from alphaloop.tools.plugins.volatility_filter.tool import VolatilityFilter
        vf = VolatilityFilter()
        vf.configure({"max_atr_pct": 0.05, "old_key": 1})
        vf.configure({"max_atr_pct": 0.10})
        assert vf.config == {"max_atr_pct": 0.10}
        assert "old_key" not in vf.config


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Live/backtest parity
# ═══════════════════════════════════════════════════════════════════════════════

class TestLiveBacktestParity:
    """Ensure VBT backtest and live loop would resolve identical params."""

    def test_same_params_same_result(self):
        """Given identical inputs, live and backtest resolve identically.

        The live loop calls resolve_construction_params(runtime_strategy, tf, asset_cfg).
        The backtest calls resolve_construction_params(strategy_payload, tf, asset_cfg).
        Both must produce the same result for the same effective input.
        """
        xau = _xau()
        strategy = {"params": {"sl_atr_mult": 1.8, "tp1_rr": 2.0}}

        # Simulate live loop call
        live_result = resolve_construction_params(strategy, "M15", xau)

        # Simulate backtest call (same inputs)
        backtest_result = resolve_construction_params(strategy, "M15", xau)

        assert live_result == backtest_result

    def test_backtest_timeframe_param_respected(self):
        """Backtest with timeframe='H1' should get H1 calibration, not M15."""
        xau = _xau()
        m15 = resolve_construction_params({}, "M15", xau)
        h1 = resolve_construction_params({}, "H1", xau)
        # These should be meaningfully different
        assert m15["sl_min_points"] != h1["sl_min_points"]

    def test_resolution_idempotent(self):
        """Calling resolve twice with same inputs gives same result."""
        xau = _xau()
        s = {"params": {"tp1_rr": 1.8}}
        r1 = resolve_construction_params(s, "M15", xau)
        r2 = resolve_construction_params(s, "M15", xau)
        assert r1 == r2


# ═══════════════════════════════════════════════════════════════════════════════
# 9. _CONSTRUCTION_KEYS exhaustiveness
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstructionKeysExhaustive:

    def test_keys_match_trade_constructor_init(self):
        """Every _CONSTRUCTION_KEYS entry must map to a TradeConstructor param."""
        from alphaloop.pipeline.construction import TradeConstructor
        import inspect
        sig = inspect.signature(TradeConstructor.__init__)
        tc_params = set(sig.parameters.keys()) - {"self", "tools"}
        # Map from resolve key → TC param name
        key_map = {
            "sl_min_points": "sl_min_pts",
            "sl_max_points": "sl_max_pts",
            "sl_atr_mult": "sl_atr_mult",
            "tp1_rr": "tp1_rr",
            "tp2_rr": "tp2_rr",
            "entry_zone_atr_mult": "entry_zone_atr_mult",
            "sl_buffer_atr": "sl_buffer_atr",
        }
        for resolve_key in _CONSTRUCTION_KEYS:
            tc_key = key_map.get(resolve_key, resolve_key)
            assert tc_key in tc_params, (
                f"resolve key '{resolve_key}' (mapped to '{tc_key}') "
                f"not found in TradeConstructor.__init__ params: {tc_params}"
            )

    def test_no_extra_keys_in_result(self):
        """resolve_construction_params must return ONLY _CONSTRUCTION_KEYS."""
        result = resolve_construction_params({}, "M15", _xau())
        assert set(result.keys()) == set(_CONSTRUCTION_KEYS)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Edge cases and regression guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_strategy_none_doesnt_crash(self):
        result = resolve_construction_params(None, "M15", _minimal_asset())
        assert result["sl_min_points"] == 100.0

    def test_asset_with_no_tf_map_attr(self):
        """Asset without default_params_by_timeframe works (falls to base)."""
        asset = SimpleNamespace(
            sl_min_points=50, sl_max_points=500, sl_atr_mult=1.0,
            tp1_rr=1.0, tp2_rr=2.0, entry_zone_atr_mult=0.2,
        )
        result = resolve_construction_params({}, "M15", asset)
        assert result["sl_min_points"] == 50.0

    def test_db_overrides_none_is_safe(self):
        result = resolve_construction_params(
            {}, "M15", _minimal_asset(), tf_db_overrides=None,
        )
        assert result is not None

    def test_empty_db_overrides_dict(self):
        result = resolve_construction_params(
            {}, "M15", _minimal_asset(), tf_db_overrides={},
        )
        assert result is not None

    def test_params_by_timeframe_none_value(self):
        """params_by_timeframe exists but value for TF is None — safe."""
        strategy = {"params_by_timeframe": {"M15": None}}
        result = resolve_construction_params(strategy, "M15", _minimal_asset())
        assert result["sl_min_points"] == 100.0

    def test_non_construction_keys_in_params_ignored(self):
        """Keys not in _CONSTRUCTION_KEYS should not appear in result."""
        strategy = {"params": {"risk_pct": 0.01, "ema_fast": 21, "sl_min_points": 200.0}}
        result = resolve_construction_params(strategy, "M15", _minimal_asset())
        assert "risk_pct" not in result
        assert "ema_fast" not in result
        assert result["sl_min_points"] == 200.0
