"""Regression test — every scoring group must have at least one tool assigned
to Stage 4B (``quality``).

Gate-1 observability measured 89/89 held cycles where ``structure``,
``volatility`` and ``momentum`` groups were stuck at the neutral 50.0 default
because the old ``STAGE_TOOL_MAP["quality"]`` list only covered ``trend``,
``volume`` and one disabled momentum tool. Gate-2 expanded that list. This
test makes the fix permanent by asserting the invariant at CI time.

If this test fails, the quality scorer is structurally incapable of producing
real signal for at least one scoring group — do not relax it; fix the map.
"""

from __future__ import annotations

import re
from pathlib import Path

from alphaloop.scoring.weights import SCORING_GROUPS
from alphaloop.tools.registry import STAGE_TOOL_MAP

_PLUGINS_DIR = Path(__file__).parent.parent.parent / "src" / "alphaloop" / "tools" / "plugins"
_GROUP_RE = re.compile(r"group\s*=\s*['\"]([a-z]+)")


def _tool_group(name: str) -> str | None:
    tool_file = _PLUGINS_DIR / name / "tool.py"
    if not tool_file.exists():
        return None
    m = _GROUP_RE.search(tool_file.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def test_quality_stage_has_tools_for_every_scoring_group():
    quality_tools = STAGE_TOOL_MAP.get("quality", [])
    assert quality_tools, "STAGE_TOOL_MAP['quality'] must not be empty"

    coverage: dict[str, list[str]] = {g: [] for g in SCORING_GROUPS}
    for tool_name in quality_tools:
        group = _tool_group(tool_name)
        if group in coverage:
            coverage[group].append(tool_name)

    missing = [g for g, tools in coverage.items() if not tools]
    assert not missing, (
        f"STAGE_TOOL_MAP['quality'] is missing tools for {missing}. "
        f"Each scoring group must have at least one plugin or Stage 4B "
        f"will return the neutral default (50.0) for that group — see "
        f"docs/references/throughput-rebalance-report.md for context. "
        f"Current coverage: {coverage}"
    )


def test_quality_stage_has_at_least_two_trend_and_momentum_tools():
    """Defence in depth — a single tool per critical group is fragile.

    If one tool is strategy-disabled, the group still has alternatives.
    """
    quality_tools = STAGE_TOOL_MAP.get("quality", [])
    coverage: dict[str, list[str]] = {"trend": [], "momentum": []}
    for tool_name in quality_tools:
        group = _tool_group(tool_name)
        if group in coverage:
            coverage[group].append(tool_name)

    for group in ("trend", "momentum"):
        assert len(coverage[group]) >= 2, (
            f"Stage 4B quality map should carry >=2 tools in the {group!r} "
            f"group so a single disabled tool cannot starve the group. "
            f"Current: {coverage[group]}"
        )
