"""
tools/registry.py
Auto-discovery and management of AlphaLoop filter tools.

Scans the plugins/ directory for subdirectories containing tool.py files.
Each tool.py must define a class that inherits from BaseTool.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Optional

from alphaloop.tools.base import BaseTool

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).parent / "plugins"

# Maps each pipeline stage to the tool plugin names that belong there.
# Used by TradingLoop._get_stage_tools() to inject the right plugins into
# each stage constructor rather than passing all tools everywhere.
STAGE_TOOL_MAP: dict[str, list[str]] = {
    "market_gate":  ["session_filter", "news_filter", "volatility_filter"],
    "regime":       ["adx_filter", "choppiness_index", "trendilo"],
    "hypothesis":   ["ema_crossover", "macd_filter", "rsi_feature", "fast_fingers"],
    "construction": ["swing_structure", "fvg_guard", "bos_guard"],
    "invalidation": ["liq_vacuum_guard", "vwap_guard"],
    "quality":      [
        "ema200_filter", "alma_filter", "bollinger_filter",
        "volume_filter", "dxy_filter", "sentiment_filter",
    ],
    "risk_gate":    ["risk_filter", "correlation_guard"],
    "exec_guard":   ["tick_jump_guard"],
}

# Default pipeline execution order
_DEFAULT_ORDER: dict[str, int] = {
    "session_filter":    1,
    "news_filter":       2,
    "volatility_filter": 3,
    "dxy_filter":        4,
    "sentiment_filter":  5,
    "risk_filter":       6,
    "ema200_filter":     7,
    "macd_filter":       8,
    "bollinger_filter":  9,
    "adx_filter":        10,
    "volume_filter":     11,
    "swing_structure":   12,
    "tick_jump_guard":   13,
    "liq_vacuum_guard":  14,
    "bos_guard":         15,
    "fvg_guard":         16,
    "vwap_guard":        17,
    "correlation_guard": 18,
    "ema_crossover":     19,
    "rsi_feature":       20,
    "trendilo":          21,
    "fast_fingers":      22,
    "choppiness_index":  23,
    "alma_filter":       24,
}


class ToolRegistry:
    """
    Auto-discovers and manages AlphaLoop filter tools.

    Discovery rules:
      - Scans plugins/ subdirectories for tool.py files
      - Each tool.py must define a class inheriting BaseTool
      - Tools are sorted by _DEFAULT_ORDER (unknown tools sort to 99)
    """

    def __init__(self) -> None:
        self._tools: dict[str, type[BaseTool]] = {}
        self._instances: dict[str, BaseTool] = {}
        self._enabled: dict[str, bool] = {}
        self._discover()

    def _discover(self) -> None:
        """Walk plugins/ subdirectories and load tool classes."""
        if not _PLUGINS_DIR.exists():
            logger.warning(f"[registry] Plugins directory not found: {_PLUGINS_DIR}")
            return

        for folder in sorted(_PLUGINS_DIR.iterdir()):
            if not folder.is_dir():
                continue
            if folder.name.startswith(("_", ".")):
                continue

            tool_file = folder / "tool.py"
            if not tool_file.exists():
                continue

            module_path = f"alphaloop.tools.plugins.{folder.name}.tool"
            try:
                mod = importlib.import_module(module_path)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseTool)
                        and attr is not BaseTool
                    ):
                        instance = attr()
                        self._tools[instance.name] = attr
                        self._instances[instance.name] = instance
                        self._enabled[instance.name] = True
                        logger.debug(f"[registry] Discovered: {instance.name} ({attr.__name__})")
                        break  # one tool per directory
            except Exception as e:
                logger.warning(f"[registry] Failed to load {module_path}: {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """Return metadata for all discovered tools, sorted by pipeline order."""
        tools = []
        for name, cls in self._tools.items():
            inst = self._instances[name]
            tools.append({
                "name": name,
                "description": inst.description,
                "enabled": self._enabled.get(name, True),
                "order": _DEFAULT_ORDER.get(name, 99),
                "class": cls.__name__,
            })
        tools.sort(key=lambda t: t["order"])
        return tools

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get a tool instance by name."""
        return self._instances.get(name)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Enable or disable a tool. Returns True if tool exists."""
        if name not in self._tools:
            return False
        self._enabled[name] = enabled
        return True

    def get_enabled_tools(self) -> list[BaseTool]:
        """Return enabled tool instances sorted by pipeline order."""
        tools = [
            (name, inst)
            for name, inst in self._instances.items()
            if self._enabled.get(name, True)
        ]
        tools.sort(key=lambda t: _DEFAULT_ORDER.get(t[0], 99))
        return [inst for _, inst in tools]

# ── Singleton ─────────────────────────────────────────────────────────────────

_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Get or create the global ToolRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
