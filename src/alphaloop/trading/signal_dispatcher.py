"""
trading/signal_dispatcher.py - Signal generation dispatcher.

Extracted from TradingLoop so that signal dispatch logic (AI hypothesis,
algo hypothesis, model routing) lives in one place and is independently
testable without instantiating the full trading loop.
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.trading.runtime_utils import current_runtime_strategy
from alphaloop.trading.strategy_loader import resolve_strategy_signal_mode

logger = logging.getLogger(__name__)


class SignalDispatcher:
    """
    Dispatches signal generation requests to the correct engine for the
    current signal_mode (ai_signal | algo_only | algo_ai).

    Held as a long-lived instance on TradingLoop so that the algo engine's
    EMA state (prev_fast, prev_slow) is preserved across cycles.

    TradingLoop must call ``update_algo_engine`` and ``update_signal_model``
    whenever a new strategy version is loaded.
    """

    def __init__(
        self,
        *,
        signal_engine: Any = None,
        ai_caller: Any = None,
        symbol: str = "",
        instance_id: str = "",
    ) -> None:
        self._signal_engine = signal_engine
        self._ai_caller = ai_caller
        self.symbol = symbol
        self.instance_id = instance_id

        # Mutable - updated by TradingLoop._ensure_strategy_loaded
        self._algo_engine: Any = None
        self.signal_model_id: str = ""

    def update_algo_engine(self, engine: Any) -> None:
        """Replace the algorithmic engine after a strategy reload."""
        self._algo_engine = engine

    def update_signal_model(self, model_id: str) -> None:
        """Update the AI signal model ID after a strategy reload."""
        self.signal_model_id = model_id

    async def dispatch(
        self,
        ctx: Any,
        regime: Any,
        *,
        signal_mode: str,
        active_strategy: Any = None,
        runtime_strategy: dict[str, Any] | None = None,
    ) -> Any:
        """
        Generate a direction hypothesis for the v4 pipeline.

        Returns ``None`` when no directional view is available.
        """
        has_strategy_contract = runtime_strategy is not None or active_strategy is not None
        runtime_strategy = current_runtime_strategy(
            runtime_strategy=runtime_strategy,
            active_strategy=active_strategy,
        )
        if not runtime_strategy:
            runtime_strategy = {
                "signal_mode": resolve_strategy_signal_mode({"signal_mode": signal_mode}),
                "signal_instruction": "",
                "ai_models": {},
            }

        effective_signal_mode = runtime_strategy.get("signal_mode", signal_mode)
        runtime_ai_models = dict(runtime_strategy.get("ai_models") or {})
        signal_model_id = str(runtime_ai_models.get("signal") or self.signal_model_id or "")

        if effective_signal_mode == "ai_signal" and self._signal_engine:
            try:
                return await self._signal_engine.generate_hypothesis(
                    ctx,
                    ai_caller=self._ai_caller,
                    model_id=signal_model_id,
                    prompt_instructions=runtime_strategy.get("signal_instruction", ""),
                    active_tools=dict(runtime_strategy.get("tools") or {}),
                )
            except Exception as e:
                logger.warning("[dispatcher] AI signal engine error: %s", e)
                return None

        if self._algo_engine:
            try:
                return await self._algo_engine.generate_hypothesis(ctx)
            except Exception as e:
                logger.warning("[dispatcher] Algo engine error: %s", e)
                return None

        logger.debug("[dispatcher] No engine configured for mode=%s", effective_signal_mode)
        return None
