"""
Strategy loader — reads active strategy config from DB and builds
runtime pipeline components for the v4 institutional trading loop.

Bridge between the stored active_strategy_{symbol} DB record and
the runtime trading loop components.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from alphaloop.config.settings_service import SettingsService
from alphaloop.scoring.weights import load_weights, load_thresholds
from alphaloop.tools.registry import ToolRegistry, _DEFAULT_ORDER

logger = logging.getLogger(__name__)


def normalize_signal_mode(raw_mode: str | None) -> str:
    """
    Normalize stored/UI signal modes to the canonical runtime values.
    """
    mode = (raw_mode or "").strip().lower()
    if mode in {"algo_only", "algo_ai", "ai_signal"}:
        return mode
    if mode == "":
        return "ai_signal"
    logger.warning("[strategy-loader] Unknown signal_mode '%s' — defaulting to ai_signal", raw_mode)
    return "ai_signal"


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
    signal_mode: str = "ai_signal"
    signal_instruction: str = ""
    validator_instruction: str = ""
    scoring_weights: dict[str, float] = field(default_factory=dict)
    confidence_thresholds: dict[str, float] = field(default_factory=dict)


async def load_active_strategy(
    settings_service: SettingsService,
    symbol: str,
    instance_id: str = "",
) -> ActiveStrategyConfig | None:
    """
    Read active strategy from DB, parse JSON, return typed config.

    Lookup order:
      1. active_strategy_{instance_id}  (per-agent binding)
      2. active_strategy_{symbol}       (legacy single-agent fallback)

    Returns None if no active strategy is set.
    """
    raw = None
    if instance_id:
        raw = await settings_service.get(f"active_strategy_{instance_id}")
    if not raw:
        raw = await settings_service.get(f"active_strategy_{symbol}")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[strategy-loader] Invalid JSON for active_strategy_%s", symbol)
        return None

    # Validate required fields and types
    if not isinstance(data, dict):
        logger.warning("[strategy-loader] Expected dict for active_strategy_%s, got %s", symbol, type(data).__name__)
        return None

    version = data.get("version", 0)
    if not isinstance(version, (int, float)):
        logger.warning("[strategy-loader] Invalid version type for active_strategy_%s", symbol)
        version = 0

    status = data.get("status", "")
    if status not in ("", "candidate", "dry_run", "demo", "live", "retired"):
        logger.warning("[strategy-loader] Unknown status '%s' for active_strategy_%s", status, symbol)

    return ActiveStrategyConfig(
        symbol=data.get("symbol", symbol),
        version=int(version),
        status=status,
        params=data.get("params", {}) if isinstance(data.get("params"), dict) else {},
        tools=data.get("tools", {}) if isinstance(data.get("tools"), dict) else {},
        validation=data.get("validation", {}) if isinstance(data.get("validation"), dict) else {},
        ai_models=data.get("ai_models", {}) if isinstance(data.get("ai_models"), dict) else {},
        signal_mode=normalize_signal_mode(data.get("signal_mode")),
        signal_instruction=data.get("signal_instruction", "") if isinstance(data.get("signal_instruction", ""), str) else "",
        validator_instruction=data.get("validator_instruction", "") if isinstance(data.get("validator_instruction", ""), str) else "",
        scoring_weights=data.get("scoring_weights", {}) if isinstance(data.get("scoring_weights"), dict) else {},
        confidence_thresholds=data.get("confidence_thresholds", {}) if isinstance(data.get("confidence_thresholds"), dict) else {},
    )


# Execution-only gates — these run post-confidence in algo_ai mode,
# not as feature providers.
_EXECUTION_GATES = {"risk_filter", "correlation_guard"}


def build_feature_pipeline(
    config: ActiveStrategyConfig,
    registry: ToolRegistry,
):
    """
    Build a FeaturePipeline for algo_ai mode.

    Includes all enabled tools EXCEPT execution gates (risk_filter,
    correlation_guard), which run separately as post-confidence guards.
    Returns a FeaturePipeline with tools in default pipeline order.
    """
    from alphaloop.tools.pipeline import FeaturePipeline

    enabled_names = [
        name for name, on in config.tools.items()
        if on and name not in _EXECUTION_GATES
    ]

    tools = []
    for name in enabled_names:
        tool = registry.get_tool(name)
        if tool:
            tools.append((name, tool))
        else:
            logger.debug("[strategy-loader] Tool '%s' not found in registry", name)

    tools.sort(key=lambda t: _DEFAULT_ORDER.get(t[0], 99))

    logger.info(
        "[strategy-loader] Built feature pipeline for %s v%d: %s",
        config.symbol,
        config.version,
        [name for name, _ in tools],
    )
    return FeaturePipeline(tools=[inst for _, inst in tools])


