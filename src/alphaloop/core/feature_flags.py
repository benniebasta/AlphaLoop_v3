"""
Feature Flags — Runtime feature toggling.

Simple in-memory feature flag system that can be controlled via settings.
Allows enabling/disabling features without code changes or restarts.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Default feature flags — all can be overridden via DB settings
_DEFAULTS: dict[str, bool] = {
    "metaloop_enabled": False,
    "micro_learner_enabled": False,
    "canary_deployment": True,
    "live_feed_enabled": False,
    "alert_engine_enabled": True,
    "portfolio_manager_enabled": False,
    "auto_report_enabled": False,
    "websocket_events": True,
    "ai_validation_stage2": True,
    "debug_logging": False,
}


class FeatureFlags:
    """
    Runtime feature flag manager.

    Reads defaults from _DEFAULTS, can be overridden by settings_service.
    Thread-safe for reads (dict lookup is atomic in CPython).
    """

    def __init__(self) -> None:
        self._flags: dict[str, bool] = dict(_DEFAULTS)
        self._overrides: dict[str, bool] = {}

    def is_enabled(self, flag: str) -> bool:
        """Check if a feature flag is enabled."""
        if flag in self._overrides:
            return self._overrides[flag]
        return self._flags.get(flag, False)

    def set_override(self, flag: str, enabled: bool) -> None:
        """Set a runtime override for a flag."""
        self._overrides[flag] = enabled
        logger.info("Feature flag '%s' overridden to %s", flag, enabled)

    def clear_override(self, flag: str) -> None:
        """Remove a runtime override, reverting to default."""
        self._overrides.pop(flag, None)

    def clear_all_overrides(self) -> None:
        self._overrides.clear()

    async def sync_from_settings(self, settings_service) -> None:
        """Sync flags from the settings service (DB)."""
        for flag in _DEFAULTS:
            key = f"FF_{flag.upper()}"
            val = await settings_service.get(key)
            if val is not None:
                self._flags[flag] = val.lower() in ("true", "1", "yes")

    def get_all(self) -> dict[str, dict]:
        """Return all flags with their current state."""
        return {
            flag: {
                "default": _DEFAULTS.get(flag, False),
                "current": self.is_enabled(flag),
                "overridden": flag in self._overrides,
            }
            for flag in {**_DEFAULTS, **self._overrides}
        }

    def __contains__(self, flag: str) -> bool:
        return self.is_enabled(flag)
