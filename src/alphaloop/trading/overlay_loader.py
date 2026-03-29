"""
Dry-run overlay loader — per-card tool overlay for experimentation.

Overlay tools run AFTER the strategy's baked-in tools in the pipeline.
Only active in dry-run mode. Ignored in live mode.
Strategy JSON remains immutable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from alphaloop.config.settings_service import SettingsService
from alphaloop.tools.registry import ToolRegistry, _DEFAULT_ORDER

logger = logging.getLogger(__name__)


@dataclass
class DryRunOverlayConfig:
    """Extra tools to append during dry-run experimentation."""

    extra_tools: list[str] = field(default_factory=list)


async def load_overlay_config(
    settings_service: SettingsService,
    symbol: str,
    version: int,
) -> DryRunOverlayConfig | None:
    """
    Read dry_run_overlay_{symbol}_v{version} from DB, parse JSON.
    Returns None if no overlay configured or key is empty.
    """
    raw = await settings_service.get(f"dry_run_overlay_{symbol}_v{version}")
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "[overlay-loader] Invalid JSON for overlay %s v%d", symbol, version,
        )
        return None

    extra = data.get("extra_tools", [])
    if not extra:
        return None

    return DryRunOverlayConfig(extra_tools=extra)


def build_overlay_pipeline(
    config: DryRunOverlayConfig,
    registry: ToolRegistry,
    exclude_tools: set[str] | None = None,
):
    """
    Build a FilterPipeline from overlay tools, excluding any already in the strategy.

    Returns a FilterPipeline with the extra tools in default pipeline order.
    """
    from alphaloop.tools.pipeline import FilterPipeline

    exclude = exclude_tools or set()
    tools = []

    for name in config.extra_tools:
        if name in exclude:
            logger.debug("[overlay-loader] Skipping '%s' (already in strategy)", name)
            continue
        tool = registry.get_tool(name)
        if tool:
            tools.append((name, tool))
        else:
            logger.debug("[overlay-loader] Tool '%s' not found in registry", name)

    tools.sort(key=lambda t: _DEFAULT_ORDER.get(t[0], 99))

    if tools:
        logger.info(
            "[overlay-loader] Built overlay pipeline: %s",
            [name for name, _ in tools],
        )

    return FilterPipeline(
        tools=[inst for _, inst in tools],
        short_circuit=True,
    )
