"""
Dry-run overlay loader — per-card tool overlay for experimentation.

Overlay tools are loaded from DB to extend a strategy's tool set during dry-run.
Only active in dry-run mode. Ignored in live mode.
Strategy JSON remains immutable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from alphaloop.config.settings_service import SettingsService

logger = logging.getLogger(__name__)


@dataclass
class DryRunOverlayConfig:
    """Extra tools to append during dry-run experimentation."""

    extra_tools: list[str] = field(default_factory=list)


def normalize_overlay_tools(extra_tools: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize overlay tool names into a stable, deduplicated string list."""
    normalized: list[str] = []
    seen: set[str] = set()
    for tool in extra_tools or []:
        name = str(tool or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


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

    return DryRunOverlayConfig(extra_tools=normalize_overlay_tools(extra))


async def save_overlay_config(
    settings_service: SettingsService,
    symbol: str,
    version: int,
    extra_tools: list[str] | tuple[str, ...] | None,
) -> DryRunOverlayConfig:
    """Persist a canonical dry-run overlay config and return the normalized value."""
    config = DryRunOverlayConfig(extra_tools=normalize_overlay_tools(extra_tools))
    await settings_service.set(
        f"dry_run_overlay_{symbol}_v{version}",
        json.dumps({"extra_tools": config.extra_tools}),
    )
    return config
