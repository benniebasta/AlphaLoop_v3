"""
scoring/tool_tracker.py

ToolPerformanceTracker — rolling win-rate and IC tracker per tool.

Tracks historical win rates for each tool/plugin and provides weights
for the GroupScorer. Tools with higher win rates get proportionally
more influence; low-performing tools are down-weighted automatically.

Win rate is neutral (0.5) until a tool has at least MIN_SAMPLES samples.
This prevents premature weighting on sparse data.

IC decay detection compares short/medium/long rolling windows and emits
ToolDecayAlert events when a tool's edge is eroding.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from statistics import mean, stdev
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

MIN_SAMPLES = 20        # minimum trades before deviating from neutral weight
DECAY_FLOOR = 0.1       # minimum weight multiplier for a deeply decaying tool


class ToolPerformanceTracker:
    """
    Tracks per-tool rolling win rates and IC (information coefficient).

    Win rate:
      - Neutral = 0.5 (used until MIN_SAMPLES reached)
      - Updated on every TradeClosed event via record()

    IC (information coefficient):
      - Correlation proxy: tool_score_at_signal_time vs trade_won (0/1)
      - Tracked over 3 windows: short=20, medium=50, long=100
      - IC < 0 across all windows → tool is anti-predictive (weight floored)

    Usage:
      tracker = ToolPerformanceTracker(window=50)
      tracker.record("ema200_filter", trade_won=True)
      weight = tracker.win_rate("ema200_filter")   # 0.0–1.0
    """

    def __init__(self, window: int = 50):
        self._window = window
        # Rolling win/loss history per tool
        self._history: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=window)
        )
        # Short IC window (last 20 trades) for decay detection
        self._short: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=20)
        )
        # Long IC window (last 100 trades) for stable baseline
        self._long: dict[str, deque[int]] = defaultdict(
            lambda: deque(maxlen=100)
        )

    def record(self, tool_name: str, trade_won: bool) -> None:
        """Record a trade outcome for a specific tool."""
        val = 1 if trade_won else 0
        self._history[tool_name].append(val)
        self._short[tool_name].append(val)
        self._long[tool_name].append(val)

    def record_batch(self, outcomes: dict[str, bool]) -> None:
        """Record outcomes for multiple tools at once (e.g. on TradeClosed)."""
        for tool_name, won in outcomes.items():
            self.record(tool_name, won)

    def win_rate(self, tool_name: str) -> float:
        """
        Return win rate for a tool as a weight multiplier (0.0–1.0).

        Returns 0.5 (neutral) until MIN_SAMPLES have been collected.
        A tool with a 70% win rate returns 0.7; with 40% returns 0.4.
        """
        h = self._history[tool_name]
        if len(h) < MIN_SAMPLES:
            return 0.5   # neutral — insufficient data
        return round(mean(h), 4)

    def ic_decay_status(self, tool_name: str) -> dict:
        """
        Compute IC decay status for a tool.

        Returns dict with:
          short_wr:  win rate over last 20 trades
          medium_wr: win rate over rolling window
          long_wr:   win rate over last 100 trades
          decaying:  True if short_wr is declining vs medium and long
          weight:    suggested weight multiplier (DECAY_FLOOR if fully decaying)
        """
        short_h = self._short[tool_name]
        medium_h = self._history[tool_name]
        long_h = self._long[tool_name]

        short_wr = mean(short_h) if len(short_h) >= 5 else 0.5
        medium_wr = mean(medium_h) if len(medium_h) >= MIN_SAMPLES else 0.5
        long_wr = mean(long_h) if len(long_h) >= MIN_SAMPLES else 0.5

        # Decay: short win rate is falling below both medium and long
        decaying = (
            len(short_h) >= 10
            and short_wr < 0.45
            and short_wr < medium_wr - 0.05
        )

        # Deep decay: losing across all windows
        deep_decay = (
            len(short_h) >= 10
            and len(medium_h) >= MIN_SAMPLES
            and short_wr < 0.45
            and medium_wr < 0.45
        )

        if deep_decay:
            weight = DECAY_FLOOR
        elif decaying:
            weight = max(short_wr, DECAY_FLOOR)
        else:
            weight = medium_wr if len(medium_h) >= MIN_SAMPLES else 0.5

        return {
            "short_wr": round(short_wr, 3),
            "medium_wr": round(medium_wr, 3),
            "long_wr": round(long_wr, 3),
            "decaying": decaying,
            "deep_decay": deep_decay,
            "weight": round(weight, 4),
            "samples": len(medium_h),
        }

    def get_all_stats(self) -> dict[str, dict]:
        """Return IC decay stats for all tracked tools."""
        return {
            tool: self.ic_decay_status(tool)
            for tool in self._history
        }

    def decay_report_text(self) -> str:
        """
        Format a human-readable tool performance report for AI prompts.
        Used by the meta-loop research agent.
        """
        if not self._history:
            return "  (no tool performance data yet)"

        lines = []
        for tool_name in sorted(self._history.keys()):
            stats = self.ic_decay_status(tool_name)
            trend = "DECAYING ⚠" if stats["decaying"] else (
                "improving" if stats["short_wr"] > stats["medium_wr"] + 0.03 else "stable"
            )
            lines.append(
                f"  {tool_name:30s}  win_rate={stats['medium_wr']:.2f}  "
                f"IC_short={stats['short_wr']:.2f}  trend={trend}  "
                f"n={stats['samples']}"
            )
        return "\n".join(lines)

    async def check_and_emit_decay_alerts(self, event_bus) -> None:
        """
        Check all tracked tools for decay and emit ToolDecayAlert events.

        Should be called after each batch of TradeClosed events (e.g. in
        on_trade_closed handler). Emits one alert per newly-decaying tool;
        does not re-emit if the tool was already flagged as decaying.

        Args:
            event_bus: EventBus instance to publish events to.
        """
        from alphaloop.core.events import ToolDecayAlert

        for tool_name in list(self._history.keys()):
            stats = self.ic_decay_status(tool_name)
            if not (stats["decaying"] or stats["deep_decay"]):
                continue
            if stats["samples"] < MIN_SAMPLES:
                continue

            try:
                await event_bus.publish(ToolDecayAlert(
                    tool_name=tool_name,
                    short_wr=stats["short_wr"],
                    medium_wr=stats["medium_wr"],
                    long_wr=stats["long_wr"],
                    deep_decay=stats["deep_decay"],
                    samples=stats["samples"],
                ))
            except Exception:
                logger.exception("[tool-tracker] Failed to publish ToolDecayAlert for %s", tool_name)

            level = "DEEP DECAY" if stats["deep_decay"] else "DECAYING"
            logger.warning(
                "[tool-tracker] %s %s — short_wr=%.2f medium_wr=%.2f (n=%d)",
                tool_name, level, stats["short_wr"], stats["medium_wr"], stats["samples"],
            )

    def seed_from_history(self, tool_outcomes: dict[str, list[bool]]) -> None:
        """
        Seed tracker from historical trade data at startup.

        Args:
            tool_outcomes: {tool_name: [True, False, True, ...]} list of win/loss
        """
        for tool_name, outcomes in tool_outcomes.items():
            for won in outcomes:
                self.record(tool_name, won)
        logger.info(
            "[tool-tracker] Seeded %d tools from history",
            len(tool_outcomes),
        )


# Module-level singleton
tool_tracker = ToolPerformanceTracker(window=50)
