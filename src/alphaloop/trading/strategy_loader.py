"""
Strategy loader — reads active strategy config from DB and builds
a FilterPipeline from the strategy's tool configuration.

Bridge between the stored active_strategy_{symbol} DB record and
the runtime trading loop components.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from alphaloop.config.settings_service import SettingsService
from alphaloop.tools.registry import ToolRegistry, _DEFAULT_ORDER

logger = logging.getLogger(__name__)


@dataclass
class ActiveStrategyConfig:
    """Parsed active strategy config from DB."""

    symbol: str
    version: int
    status: str
    params: dict = field(default_factory=dict)
    tools: dict[str, bool] = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    ai_models: dict[str, str] = field(default_factory=dict)
    signal_mode: str = "algo_plus_ai"


async def load_active_strategy(
    settings_service: SettingsService,
    symbol: str,
) -> ActiveStrategyConfig | None:
    """
    Read active_strategy_{symbol} from DB, parse JSON, return typed config.
    Returns None if no active strategy is set.
    """
    raw = await settings_service.get(f"active_strategy_{symbol}")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[strategy-loader] Invalid JSON for active_strategy_%s", symbol)
        return None

    return ActiveStrategyConfig(
        symbol=data.get("symbol", symbol),
        version=data.get("version", 0),
        status=data.get("status", ""),
        params=data.get("params", {}),
        tools=data.get("tools", {}),
        validation=data.get("validation", {}),
        ai_models=data.get("ai_models", {}),
        signal_mode=data.get("signal_mode", "algo_plus_ai"),
    )


def build_strategy_pipeline(
    config: ActiveStrategyConfig,
    registry: ToolRegistry,
):
    """
    Build a FilterPipeline using ONLY the tools marked true in config.tools.

    Tools not in the strategy's tools map are excluded.
    Returns a FilterPipeline with tools in default pipeline order.
    """
    from alphaloop.tools.pipeline import FilterPipeline

    enabled_names = [name for name, on in config.tools.items() if on]

    tools = []
    for name in enabled_names:
        tool = registry.get_tool(name)
        if tool:
            tools.append((name, tool))
        else:
            logger.debug("[strategy-loader] Tool '%s' not found in registry", name)

    # Sort by default pipeline order
    tools.sort(key=lambda t: _DEFAULT_ORDER.get(t[0], 99))

    logger.info(
        "[strategy-loader] Built pipeline for %s v%d: %s",
        config.symbol,
        config.version,
        [name for name, _ in tools],
    )
    return FilterPipeline(
        tools=[inst for _, inst in tools],
        short_circuit=True,
    )
