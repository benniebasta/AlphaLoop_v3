from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from alphaloop.trading.loop import TradingLoop
from alphaloop.trading.runtime_utils import current_strategy_reference
from alphaloop.trading.strategy_loader import ActiveStrategyConfig, StrategySpecV1


def _make_strategy(*, validator_instruction: str) -> ActiveStrategyConfig:
    return ActiveStrategyConfig(
        symbol="XAUUSD",
        version=7,
        status="live",
        signal_mode="ai_signal",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={},
        ai_models={"signal": "gpt-5.4-mini"},
        signal_instruction="signal prompt",
        validator_instruction=validator_instruction,
        strategy_spec=StrategySpecV1(
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            prompt_bundle={
                "signal_instruction": "signal prompt",
                "validator_instruction": validator_instruction,
            },
        ),
    )


@pytest.mark.asyncio
async def test_ensure_strategy_loaded_rebuilds_when_prompt_bundle_changes(monkeypatch):
    first = _make_strategy(validator_instruction="validator prompt v1")
    second = _make_strategy(validator_instruction="validator prompt v2")

    load_calls = AsyncMock(side_effect=[first, second])
    build_calls: list[int] = []
    algo_setup_tags: list[str] = []

    async def fake_load_active_strategy(settings_service, symbol, instance_id):
        return await load_calls(settings_service, symbol, instance_id)

    def fake_build_feature_pipeline(config, registry):
        build_calls.append(config.version)
        return {"version": config.version}

    class FakeAlgorithmicSignalEngine:
        def __init__(self, symbol, params, prev_ema_state=None, setup_tag="pullback_continuation"):
            self.symbol = symbol
            self.params = params
            self._prev_fast = None
            self._prev_slow = None
            algo_setup_tags.append(setup_tag)

    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.load_active_strategy",
        fake_load_active_strategy,
    )
    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.build_feature_pipeline",
        fake_build_feature_pipeline,
    )
    monkeypatch.setattr(
        "alphaloop.signals.algorithmic.AlgorithmicSignalEngine",
        FakeAlgorithmicSignalEngine,
    )

    loop = TradingLoop(
        symbol="XAUUSD",
        instance_id="bot-1",
        dry_run=True,
        settings_service=SimpleNamespace(),
        tool_registry=SimpleNamespace(get_tool=lambda name: None),
    )

    await loop._ensure_strategy_loaded()
    await loop._ensure_strategy_loaded()

    assert loop._active_strategy is second
    assert loop._strategy_runtime_sig
    assert loop._active_strategy.validator_instruction == "validator prompt v2"
    assert algo_setup_tags == ["pullback", "pullback"]
    assert build_calls == []


@pytest.mark.asyncio
async def test_ensure_strategy_loaded_clears_stale_signal_model_when_runtime_has_none(monkeypatch):
    first = _make_strategy(validator_instruction="validator prompt v1")
    second = ActiveStrategyConfig(
        symbol="XAUUSD",
        version=8,
        status="live",
        signal_mode="ai_signal",
        params={"risk_pct": 0.01},
        tools={"session_filter": True},
        validation={},
        ai_models={},
        signal_instruction="signal prompt",
        validator_instruction="validator prompt v2",
        strategy_spec=StrategySpecV1(
            spec_version="v1",
            signal_mode="ai_signal",
            setup_family="discretionary_ai",
            prompt_bundle={
                "signal_instruction": "signal prompt",
                "validator_instruction": "validator prompt v2",
            },
        ),
    )

    load_calls = AsyncMock(side_effect=[first, second])

    async def fake_load_active_strategy(settings_service, symbol, instance_id):
        return await load_calls(settings_service, symbol, instance_id)

    def fake_build_feature_pipeline(config, registry):
        return {"version": config.version}

    class FakeAlgorithmicSignalEngine:
        def __init__(self, symbol, params, prev_ema_state=None, setup_tag="pullback_continuation"):
            self.symbol = symbol
            self.params = params
            self._prev_fast = None
            self._prev_slow = None

    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.load_active_strategy",
        fake_load_active_strategy,
    )
    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.build_feature_pipeline",
        fake_build_feature_pipeline,
    )
    monkeypatch.setattr(
        "alphaloop.signals.algorithmic.AlgorithmicSignalEngine",
        FakeAlgorithmicSignalEngine,
    )

    loop = TradingLoop(
        symbol="XAUUSD",
        instance_id="bot-1",
        dry_run=True,
        settings_service=SimpleNamespace(),
        tool_registry=SimpleNamespace(get_tool=lambda name: None),
    )

    await loop._ensure_strategy_loaded()
    assert loop.signal_model_id == "gpt-5.4-mini"

    await loop._ensure_strategy_loaded()

    assert loop.signal_model_id == ""


@pytest.mark.asyncio
async def test_ensure_strategy_loaded_clears_dispatcher_and_models_when_no_strategy(monkeypatch):
    async def fake_load_active_strategy(settings_service, symbol, instance_id):
        return None

    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.load_active_strategy",
        fake_load_active_strategy,
    )

    loop = TradingLoop(
        symbol="XAUUSD",
        instance_id="bot-1",
        dry_run=True,
        settings_service=SimpleNamespace(),
        tool_registry=SimpleNamespace(get_tool=lambda name: None),
    )
    loop._active_strategy = SimpleNamespace(version=7)
    loop._runtime_strategy = {"version": 7, "spec_version": "v1"}
    loop._feature_pipeline = {"version": 7}
    loop._algo_engine = object()
    loop._strategy_runtime_sig = "stale-runtime"
    loop.signal_model_id = "stale-model"
    loop._signal_dispatcher.update_algo_engine(object())
    loop._signal_dispatcher.update_signal_model("stale-model")
    loop._execution_orch.update_state(
        active_strategy=SimpleNamespace(version=7),
        canary_allocation=0.25,
    )
    loop._canary_allocation = 0.25

    await loop._ensure_strategy_loaded()

    assert loop._active_strategy is None
    assert loop._runtime_strategy == {}
    assert loop._feature_pipeline is None
    assert loop._algo_engine is None
    assert loop._strategy_runtime_sig == ""
    assert loop.signal_model_id == ""
    assert loop._signal_dispatcher._algo_engine is None
    assert loop._signal_dispatcher.signal_model_id == ""
    assert loop._execution_orch._active_strategy is None


@pytest.mark.asyncio
async def test_ensure_strategy_loaded_builds_algo_engine_from_spec_first_entry_model(monkeypatch):
    strategy = ActiveStrategyConfig(
        symbol="XAUUSD",
        version=8,
        status="live",
        signal_mode="algo_ai",
        params={
            "risk_pct": 0.01,
            "signal_rules": [{"source": "ema_crossover"}],
            "signal_logic": "AND",
        },
        tools={},
        validation={},
        ai_models={},
        signal_instruction="signal prompt",
        validator_instruction="validator prompt",
        strategy_spec=StrategySpecV1(
            spec_version="v1",
            signal_mode="algo_ai",
            setup_family="momentum_expansion",
            entry_model={
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        ),
    )

    captured_params: list[dict] = []

    async def fake_load_active_strategy(settings_service, symbol, instance_id):
        return strategy

    def fake_build_feature_pipeline(config, registry):
        return {"version": config.version}

    class FakeAlgorithmicSignalEngine:
        def __init__(self, symbol, params, prev_ema_state=None, setup_tag="pullback_continuation"):
            self.symbol = symbol
            self.params = params
            self._prev_fast = None
            self._prev_slow = None
            captured_params.append(dict(params))

    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.load_active_strategy",
        fake_load_active_strategy,
    )
    monkeypatch.setattr(
        "alphaloop.trading.strategy_loader.build_feature_pipeline",
        fake_build_feature_pipeline,
    )
    monkeypatch.setattr(
        "alphaloop.signals.algorithmic.AlgorithmicSignalEngine",
        FakeAlgorithmicSignalEngine,
    )

    loop = TradingLoop(
        symbol="XAUUSD",
        instance_id="bot-1",
        dry_run=True,
        settings_service=SimpleNamespace(),
        tool_registry=SimpleNamespace(get_tool=lambda name: None),
    )

    await loop._ensure_strategy_loaded()

    assert captured_params == [
        {
            "risk_pct": 0.01,
            "signal_rules": [{"source": "macd_crossover"}],
            "signal_logic": "OR",
        }
    ]


def test_current_strategy_reference_prefers_spec_first_runtime_context():
    loop = TradingLoop(symbol="XAUUSD", instance_id="bot-1", dry_run=True)
    loop._active_strategy = SimpleNamespace(
        version="legacy",
        signal_mode="algo_only",
        strategy_spec=SimpleNamespace(spec_version="v1"),
    )
    loop._runtime_strategy = {"version": 0, "spec_version": "v1"}

    assert current_strategy_reference(
        symbol=loop.symbol,
        runtime_strategy=loop._runtime_strategy,
        active_strategy=loop._active_strategy,
    )["strategy_id"] == "XAUUSD"


def test_active_strategy_runtime_helpers_prefer_cached_runtime_snapshot():
    loop = TradingLoop(symbol="XAUUSD", instance_id="bot-1", dry_run=True)
    loop._active_strategy = SimpleNamespace(
        version="legacy",
        params={"risk_pct": 9.99, "signal_rules": [{"source": "ema_crossover"}], "signal_logic": "AND"},
        signal_mode="algo_only",
        strategy_spec=SimpleNamespace(spec_version="v1"),
    )
    loop._runtime_strategy = {
        "version": 11,
        "params": {"risk_pct": 0.01, "signal_rules": [{"source": "macd_crossover"}], "signal_logic": "OR"},
        "spec_version": "v1",
    }

    assert current_strategy_reference(
        symbol=loop.symbol,
        runtime_strategy=loop._runtime_strategy,
        active_strategy=loop._active_strategy,
    )["strategy_id"] == "XAUUSD.v11"
    assert dict(loop._active_strategy_runtime().get("params") or {}) == {
        "risk_pct": 0.01,
        "signal_rules": [{"source": "macd_crossover"}],
        "signal_logic": "OR",
    }


@pytest.mark.asyncio
async def test_submit_execution_prefers_spec_first_runtime_version(monkeypatch):
    captured = {}

    async def _mock_execute_market_order(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="FILLED", broker_ticket=123, fill_price=2350.0)

    loop = TradingLoop(symbol="XAUUSD", instance_id="bot-1", dry_run=True)
    loop._execution_service = MagicMock(execute_market_order=_mock_execute_market_order)
    loop._active_strategy = SimpleNamespace(
        version="legacy",
        signal_mode="algo_only",
        strategy_spec=SimpleNamespace(spec_version="v1"),
    )
    loop.risk_monitor = None
    loop.sizer = None

    await loop._submit_execution(
        signal=SimpleNamespace(direction="BUY"),
        sizing={"lots": 0.1},
        stop_loss=2340.0,
        take_profit=2360.0,
        validated={"ok": True},
        context={},
    )

    assert captured["strategy_id"] == "XAUUSD"
    assert captured["strategy_version"] == ""


def test_get_stage_tools_normalizes_list_style_runtime_tools():
    tool = object()
    loop = TradingLoop(
        symbol="XAUUSD",
        instance_id="bot-1",
        dry_run=True,
        tool_registry=SimpleNamespace(get_tool=lambda name: tool if name == "session_filter" else None),
    )
    loop._active_strategy = SimpleNamespace(
        signal_mode="algo_ai",
        tools=["session_filter"],
        strategy_spec=SimpleNamespace(spec_version="v1"),
    )

    tools = loop._get_stage_tools("market_gate")

    assert tools == [tool]


def test_active_strategy_params_prefers_spec_first_entry_model():
    loop = TradingLoop(symbol="XAUUSD", instance_id="bot-1", dry_run=True)
    loop._active_strategy = SimpleNamespace(
        params={"risk_pct": 0.01, "signal_rules": [{"source": "ema_crossover"}], "signal_logic": "AND"},
        signal_mode="algo_ai",
        strategy_spec=SimpleNamespace(
            spec_version="v1",
            signal_mode="algo_ai",
            entry_model={
                "signal_rule_sources": ["macd_crossover"],
                "signal_logic": "OR",
            },
        ),
    )

    params = dict(loop._active_strategy_runtime().get("params") or {})

    assert params["risk_pct"] == 0.01
    assert params["signal_rules"] == [{"source": "macd_crossover"}]
    assert params["signal_logic"] == "OR"


@pytest.mark.asyncio
async def test_cycle_v4_prefers_spec_first_runtime_validator_model(monkeypatch):
    captured = {}

    class FakeValidator:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeOrchestrator:
        def __init__(self):
            self.ai_validator = None

        async def check_delayed(self, context, symbol):
            return None

        async def run(self, context, generate_signal, symbol, mode):
            raise RuntimeError("stop after validator wiring")

    loop = TradingLoop(symbol="XAUUSD", instance_id="bot-1", dry_run=True)
    loop.ai_caller = object()
    loop._active_strategy = SimpleNamespace(
        signal_mode="ai_signal",
        ai_models={"validator": "stale-validator"},
        validator_instruction="stale validator prompt",
        strategy_spec=SimpleNamespace(
            spec_version="v1",
            signal_mode="ai_signal",
            prompt_bundle={"validator_instruction": "spec validator prompt"},
            ai_models={"validator": "spec-validator"},
        ),
    )

    monkeypatch.setattr(
        "alphaloop.pipeline.ai_validator.BoundedAIValidator",
        FakeValidator,
    )
    monkeypatch.setattr(loop, "_build_v4_orchestrator", lambda: FakeOrchestrator())

    with pytest.raises(RuntimeError, match="stop after validator wiring"):
        await loop._cycle_v4(context={}, signal_mode="ai_signal", t0=0.0)

    assert captured["validator_model"] == "spec-validator"
    assert captured["validator_instruction"] == "spec validator prompt"
