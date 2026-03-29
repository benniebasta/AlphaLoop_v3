"""
seedlab/registry.py — Card persistence (save/load strategy cards).

Stores strategy cards as JSON files in a registry directory,
providing save, load, list, and search capabilities.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from alphaloop.seedlab.strategy_card import StrategyCard

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "strategy_cards"


class CardRegistry:
    """
    File-based strategy card registry.

    Cards are stored as individual JSON files named by seed_hash.
    """

    def __init__(self, registry_dir: Path | str | None = None) -> None:
        self._dir = Path(registry_dir) if registry_dir else DEFAULT_REGISTRY_DIR

    def save(self, card: StrategyCard) -> Path:
        """Save a strategy card to the registry. Returns the file path."""
        self._dir.mkdir(parents=True, exist_ok=True)
        filename = f"{card.symbol}_{card.seed_hash}.json"
        path = self._dir / filename

        # Atomic write
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            card.model_dump_json(indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))

        logger.info("Saved card %r to %s", card.name, path)
        return path

    def load(self, symbol: str, seed_hash: str) -> StrategyCard | None:
        """Load a strategy card by symbol and seed hash."""
        filename = f"{symbol}_{seed_hash}.json"
        path = self._dir / filename
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return StrategyCard.model_validate(data)
        except Exception as exc:
            logger.error("Failed to load card from %s: %s", path, exc)
            return None

    def list_cards(
        self,
        symbol: str | None = None,
        status: str | None = None,
    ) -> list[StrategyCard]:
        """List all cards in the registry, optionally filtered."""
        if not self._dir.exists():
            return []

        cards: list[StrategyCard] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                card = StrategyCard.model_validate(data)
                if symbol and card.symbol != symbol:
                    continue
                if status and card.status != status:
                    continue
                cards.append(card)
            except Exception as exc:
                logger.warning("Skipping invalid card %s: %s", path.name, exc)

        return cards

    def get_top_cards(
        self,
        symbol: str | None = None,
        limit: int = 10,
    ) -> list[StrategyCard]:
        """Get top-scoring cards, sorted by total_score descending."""
        cards = self.list_cards(symbol=symbol, status="candidate")
        cards.sort(key=lambda c: c.total_score, reverse=True)
        return cards[:limit]

    def delete(self, symbol: str, seed_hash: str) -> bool:
        """Delete a card from the registry."""
        filename = f"{symbol}_{seed_hash}.json"
        path = self._dir / filename
        if path.exists():
            path.unlink()
            logger.info("Deleted card %s", filename)
            return True
        return False
