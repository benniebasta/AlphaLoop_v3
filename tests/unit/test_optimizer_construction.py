"""Unit tests for optimizer.py construction-aware extensions."""

import pytest
import optuna

from alphaloop.backtester.optimizer import suggest_construction_params
from alphaloop.backtester.params import BacktestParams


class TestSuggestConstructionParams:
    def test_returns_required_keys(self):
        """All construction params should be present."""
        study = optuna.create_study()
        trial = study.ask()
        base = BacktestParams(
            signal_mode="ai_signal",
            setup_family="momentum_expansion",
            strategy_spec={"setup_family": "momentum_expansion"},
            tools={"session_filter": True},
            source="meta_loop",
        )
        params = suggest_construction_params(trial, base)

        assert "tp1_rr" in params
        assert "tp2_rr" in params
        assert "sl_min_points" in params
        assert "sl_max_points" in params
        assert "sl_buffer_atr" in params
        assert "confidence_threshold" in params
        assert "entry_zone_atr_mult" in params
        assert "ema_fast" in params
        assert "ema_slow" in params
        assert "signal_rules" in params
        assert params["signal_mode"] == "ai_signal"
        assert params["setup_family"] == "momentum_expansion"
        assert params["strategy_spec"]["setup_family"] == "momentum_expansion"
        assert params["tools"] == {"session_filter": True}
        assert params["source"] == "meta_loop"

    def test_prefers_strategy_spec_metadata_over_stale_flat_fields(self):
        """Construction params should preserve spec-first strategy metadata."""
        study = optuna.create_study()
        trial = study.ask()
        base = BacktestParams(
            signal_mode="algo_only",
            setup_family="pullback_continuation",
            strategy_spec={
                "setup_family": "discretionary_ai",
                "signal_mode": "ai_signal",
                "metadata": {"source": "ui_ai_signal_card"},
            },
            tools={"session_filter": True},
            source="legacy_flat_source",
        )
        params = suggest_construction_params(trial, base)

        assert params["signal_mode"] == "ai_signal"
        assert params["setup_family"] == "discretionary_ai"
        assert params["source"] == "ui_ai_signal_card"
        assert params["strategy_spec"]["metadata"]["source"] == "ui_ai_signal_card"

    def test_prefers_strategy_spec_entry_model_rules_and_logic(self):
        study = optuna.create_study()
        trial = study.ask()
        base = BacktestParams(
            signal_mode="algo_ai",
            setup_family="momentum_expansion",
            strategy_spec={
                "setup_family": "momentum_expansion",
                "signal_mode": "algo_ai",
                "entry_model": {
                    "signal_rule_sources": ["macd_crossover"],
                    "signal_logic": "OR",
                },
            },
            signal_rules=[{"source": "ema_crossover"}],
            signal_logic="AND",
        )

        params = suggest_construction_params(trial, base)

        assert params["signal_rules"] == [{"source": "macd_crossover"}]
        assert params["signal_logic"] == "OR"

    def test_no_sl_atr_mult(self):
        """sl_atr_mult should NOT be suggested (SL is structure-derived)."""
        study = optuna.create_study()
        trial = study.ask()
        base = BacktestParams()
        params = suggest_construction_params(trial, base)

        assert "sl_atr_mult" not in params

    def test_tp2_greater_than_tp1(self):
        """tp2_rr should always be > tp1_rr."""
        study = optuna.create_study()
        for _ in range(10):
            trial = study.ask()
            try:
                params = suggest_construction_params(trial, BacktestParams())
                assert params["tp2_rr"] > params["tp1_rr"]
            except optuna.TrialPruned:
                pass  # pruned trials are OK

    def test_sl_max_greater_than_sl_min(self):
        """sl_max_points should always be > sl_min_points."""
        study = optuna.create_study()
        for _ in range(10):
            trial = study.ask()
            try:
                params = suggest_construction_params(trial, BacktestParams())
                assert params["sl_max_points"] > params["sl_min_points"]
            except optuna.TrialPruned:
                pass

    def test_ema_fast_less_than_slow(self):
        """ema_fast < ema_slow or trial is pruned."""
        study = optuna.create_study()
        for _ in range(10):
            trial = study.ask()
            try:
                params = suggest_construction_params(trial, BacktestParams())
                assert params["ema_fast"] < params["ema_slow"]
            except optuna.TrialPruned:
                pass


class TestStrategyCardConstructionFields:
    def test_card_has_construction_fields(self):
        """StrategyCard should have construction stat fields with defaults."""
        from alphaloop.seedlab.strategy_card import StrategyCard
        # Create with defaults — construction fields should be 0/empty
        card = StrategyCard(
            name="test",
            seed_hash="abc",
            symbol="XAUUSD",
            category="test",
        )
        assert card.total_opportunities == 0
        assert card.valid_constructed == 0
        assert card.skipped_reasons == {}
        assert card.execution_rate == 0.0
        assert card.avg_sl_distance_pts == 0.0
        assert card.avg_rr_actual == 0.0
