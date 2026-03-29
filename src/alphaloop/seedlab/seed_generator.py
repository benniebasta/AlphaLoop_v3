"""
seedlab/seed_generator.py — Template + combinatorial seed generation.

Generates valid filter combinations (seeds) for strategy backtesting.
Each seed represents a unique combination of filters to test.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MIN_FILTERS = 3
MAX_FILTERS = 7


class StrategySeed(BaseModel):
    """Immutable descriptor for a filter combination to be backtested."""

    seed_hash: str
    name: str
    category: str  # trend / scalping / liquidity / volatility / hybrid
    filters: tuple[str, ...] = Field(default_factory=tuple)
    description: str = ""

    @property
    def filter_count(self) -> int:
        return len(self.filters)

    model_config = {"frozen": True}


def compute_seed_hash(filter_names: list[str]) -> str:
    """Deterministic hash for a sorted list of filter names."""
    canonical = "|".join(sorted(filter_names))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Predefined seed templates
# ---------------------------------------------------------------------------

_SEED_TEMPLATES: list[dict[str, Any]] = [
    # Trend seeds
    {
        "name": "Trend-Core",
        "category": "trend",
        "filters": ["ema200_trend", "bos_guard", "session_filter", "volatility_filter"],
        "description": "Classic trend-following: EMA200 + BOS + session/vol gates.",
    },
    {
        "name": "Trend-Full",
        "category": "trend",
        "filters": [
            "ema200_trend", "bos_guard", "fvg_guard",
            "session_filter", "volatility_filter",
        ],
        "description": "Full structural trend: EMA200 + BOS + FVG + session/vol.",
    },
    {
        "name": "Trend-Guarded",
        "category": "trend",
        "filters": [
            "ema200_trend", "bos_guard", "liq_vacuum_guard",
            "vwap_guard", "session_filter", "volatility_filter",
        ],
        "description": "Trend with full guard suite.",
    },
    # Scalping seeds
    {
        "name": "Scalp-Session",
        "category": "scalping",
        "filters": ["session_filter", "volatility_filter", "ema200_trend"],
        "description": "Session-focused scalping with trend filter.",
    },
    {
        "name": "Scalp-VWAP",
        "category": "scalping",
        "filters": ["session_filter", "volatility_filter", "vwap_guard", "ema200_trend"],
        "description": "VWAP mean-reversion scalping.",
    },
    # Liquidity seeds
    {
        "name": "Liq-FVG",
        "category": "liquidity",
        "filters": [
            "fvg_guard", "liq_vacuum_guard", "vwap_guard",
            "session_filter", "volatility_filter",
        ],
        "description": "Liquidity-driven: FVG + vacuum + VWAP.",
    },
    {
        "name": "Liq-Structure",
        "category": "liquidity",
        "filters": ["fvg_guard", "bos_guard", "liq_vacuum_guard", "session_filter"],
        "description": "Structure + liquidity.",
    },
    # Volatility seeds
    {
        "name": "Vol-Guard",
        "category": "volatility",
        "filters": [
            "volatility_filter", "liq_vacuum_guard", "ema200_trend", "session_filter",
        ],
        "description": "Volatility-aware with liq vacuum.",
    },
    # Hybrid seeds
    {
        "name": "Hybrid-Full",
        "category": "hybrid",
        "filters": [
            "ema200_trend", "bos_guard", "fvg_guard",
            "liq_vacuum_guard", "vwap_guard",
            "session_filter", "volatility_filter",
        ],
        "description": "Maximum filter coverage.",
    },
    {
        "name": "Hybrid-Adaptive",
        "category": "hybrid",
        "filters": [
            "ema200_trend", "bos_guard", "session_filter",
            "volatility_filter", "equity_curve_guard",
        ],
        "description": "Trend + equity curve scaling.",
    },
]


def generate_template_seeds() -> list[StrategySeed]:
    """Generate all predefined seed templates. Returns only valid seeds."""
    seeds: list[StrategySeed] = []
    for t in _SEED_TEMPLATES:
        filters = t["filters"]
        if not (MIN_FILTERS <= len(filters) <= MAX_FILTERS):
            logger.warning("Template %r has %d filters — skipped", t["name"], len(filters))
            continue
        sorted_filters = tuple(sorted(filters))
        seed = StrategySeed(
            seed_hash=compute_seed_hash(list(sorted_filters)),
            name=t["name"],
            category=t["category"],
            filters=sorted_filters,
            description=t.get("description", ""),
        )
        seeds.append(seed)
    return seeds


def generate_combinatorial_seeds(
    available_filters: list[str] | None = None,
    min_filters: int = MIN_FILTERS,
    max_filters: int = MAX_FILTERS,
    max_seeds: int = 50,
) -> list[StrategySeed]:
    """
    Generate seeds by combinatorial enumeration.

    Args:
        available_filters: Filter names to combine. Uses defaults if None.
        min_filters: Minimum filters per seed.
        max_filters: Maximum filters per seed.
        max_seeds: Cap on total seeds generated.

    Returns:
        List of unique StrategySeed objects.
    """
    if available_filters is None:
        available_filters = [
            "ema200_trend", "bos_guard", "fvg_guard", "liq_vacuum_guard",
            "vwap_guard", "session_filter", "volatility_filter",
            "equity_curve_guard",
        ]

    seen_hashes: set[str] = set()
    seeds: list[StrategySeed] = []

    for r in range(min_filters, min(max_filters + 1, len(available_filters) + 1)):
        for combo in itertools.combinations(available_filters, r):
            names = sorted(combo)
            h = compute_seed_hash(names)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            # Auto-categorize
            has_trend = "ema200_trend" in names
            has_fvg = "fvg_guard" in names
            has_equity = "equity_curve_guard" in names
            guard_count = sum(1 for n in names if "guard" in n)

            if has_equity or (has_trend and guard_count >= 2):
                cat = "hybrid"
            elif has_trend and guard_count == 0:
                cat = "trend"
            elif has_fvg and guard_count >= 2:
                cat = "liquidity"
            elif guard_count >= 2:
                cat = "volatility"
            else:
                cat = "scalping"

            seed = StrategySeed(
                seed_hash=h,
                name=f"Auto-{cat.title()}-{h[:6]}",
                category=cat,
                filters=tuple(names),
                description=f"Auto-generated {cat} seed with {len(names)} filters.",
            )
            seeds.append(seed)

            if len(seeds) >= max_seeds:
                return seeds

    return seeds
