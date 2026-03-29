"""
seedlab/evolution.py — Evolutionary strategy search.

Implements genetic algorithm operations on strategy seeds:
  - Mutation: swap/add/remove filters from existing seeds
  - Crossover: combine filters from two parent seeds
  - Tournament selection: pick parents by score
  - Generation loop: evolve a population over N generations

Operates on StrategySeed objects from seed_generator.py.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable

from alphaloop.seedlab.seed_generator import (
    MAX_FILTERS,
    MIN_FILTERS,
    StrategySeed,
    compute_seed_hash,
)

logger = logging.getLogger(__name__)

# All possible filters for mutation
ALL_AVAILABLE_FILTERS = [
    "ema200_trend", "bos_guard", "fvg_guard", "liq_vacuum_guard",
    "vwap_guard", "session_filter", "volatility_filter",
    "equity_curve_guard", "tick_jump_guard",
]


def mutate_seed(
    seed: StrategySeed,
    mutation_rate: float = 0.3,
    available_filters: list[str] | None = None,
) -> StrategySeed | None:
    """
    Mutate a seed by randomly swapping, adding, or removing a filter.

    Returns a new StrategySeed or None if mutation produces an invalid seed.
    """
    filters_pool = available_filters or ALL_AVAILABLE_FILTERS
    filters = list(seed.filters)

    if random.random() > mutation_rate:
        return None  # No mutation this round

    op = random.choice(["swap", "add", "remove"])

    if op == "swap" and filters:
        # Replace a random filter with one not already present
        unused = [f for f in filters_pool if f not in filters]
        if unused:
            idx = random.randint(0, len(filters) - 1)
            filters[idx] = random.choice(unused)

    elif op == "add":
        if len(filters) < MAX_FILTERS:
            unused = [f for f in filters_pool if f not in filters]
            if unused:
                filters.append(random.choice(unused))

    elif op == "remove":
        if len(filters) > MIN_FILTERS:
            idx = random.randint(0, len(filters) - 1)
            filters.pop(idx)

    filters = sorted(set(filters))
    if not (MIN_FILTERS <= len(filters) <= MAX_FILTERS):
        return None

    new_hash = compute_seed_hash(filters)
    if new_hash == seed.seed_hash:
        return None  # Identical to parent

    return StrategySeed(
        seed_hash=new_hash,
        name=f"Evo-Mut-{new_hash[:6]}",
        category=seed.category,
        filters=tuple(filters),
        description=f"Mutation of {seed.name}: {op} filter.",
    )


def crossover_seeds(
    parent_a: StrategySeed,
    parent_b: StrategySeed,
) -> StrategySeed | None:
    """
    Crossover two parent seeds by combining their filter sets.

    Takes a random subset from each parent's filters.
    Returns a new StrategySeed or None if result is invalid/duplicate.
    """
    filters_a = set(parent_a.filters)
    filters_b = set(parent_b.filters)
    all_filters = list(filters_a | filters_b)

    if len(all_filters) < MIN_FILTERS:
        return None

    # Random subset of size between min and max
    size = random.randint(MIN_FILTERS, min(MAX_FILTERS, len(all_filters)))
    child_filters = sorted(random.sample(all_filters, size))

    new_hash = compute_seed_hash(child_filters)
    if new_hash == parent_a.seed_hash or new_hash == parent_b.seed_hash:
        return None

    # Inherit category from more dominant parent
    cat = parent_a.category if random.random() < 0.5 else parent_b.category

    return StrategySeed(
        seed_hash=new_hash,
        name=f"Evo-Cross-{new_hash[:6]}",
        category=cat,
        filters=tuple(child_filters),
        description=f"Crossover of {parent_a.name} x {parent_b.name}.",
    )


def tournament_select(
    population: list[tuple[StrategySeed, float]],
    tournament_size: int = 3,
) -> StrategySeed:
    """Select a parent via tournament selection (pick best of N random)."""
    contestants = random.sample(population, min(tournament_size, len(population)))
    best = max(contestants, key=lambda x: x[1])
    return best[0]


def evolve_generation(
    scored_population: list[tuple[StrategySeed, float]],
    population_size: int = 20,
    mutation_rate: float = 0.3,
    crossover_rate: float = 0.5,
    elite_count: int = 3,
) -> list[StrategySeed]:
    """
    Evolve one generation from a scored population.

    Args:
        scored_population: List of (seed, score) tuples, sorted by score desc.
        population_size: Target size for the next generation.
        mutation_rate: Probability of mutation per offspring.
        crossover_rate: Fraction of offspring produced by crossover.
        elite_count: Number of top seeds to keep unchanged.

    Returns:
        New generation of seeds (may include duplicates removed).
    """
    if len(scored_population) < 2:
        return [s for s, _ in scored_population]

    # Sort by score descending
    scored_population = sorted(scored_population, key=lambda x: x[1], reverse=True)

    next_gen: list[StrategySeed] = []
    seen_hashes: set[str] = set()

    # Elitism: keep top N unchanged
    for seed, score in scored_population[:elite_count]:
        if seed.seed_hash not in seen_hashes:
            next_gen.append(seed)
            seen_hashes.add(seed.seed_hash)

    # Fill remaining via crossover and mutation
    attempts = 0
    max_attempts = population_size * 10

    while len(next_gen) < population_size and attempts < max_attempts:
        attempts += 1

        if random.random() < crossover_rate and len(scored_population) >= 2:
            # Crossover
            parent_a = tournament_select(scored_population)
            parent_b = tournament_select(scored_population)
            child = crossover_seeds(parent_a, parent_b)
        else:
            # Mutation
            parent = tournament_select(scored_population)
            child = mutate_seed(parent, mutation_rate=mutation_rate)

        if child is None or child.seed_hash in seen_hashes:
            continue

        next_gen.append(child)
        seen_hashes.add(child.seed_hash)

    logger.info(
        "Evolution: %d parents -> %d offspring (%d attempts, %d elite)",
        len(scored_population), len(next_gen), attempts, elite_count,
    )
    return next_gen


class EvolutionarySearch:
    """
    Multi-generation evolutionary search for optimal filter combinations.

    Runs over N generations, evaluating each seed via a score function,
    and evolving the population toward higher-scoring seeds.
    """

    def __init__(
        self,
        initial_seeds: list[StrategySeed],
        population_size: int = 20,
        generations: int = 5,
        mutation_rate: float = 0.3,
        crossover_rate: float = 0.5,
        elite_count: int = 3,
    ) -> None:
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_count = elite_count
        self._initial_seeds = initial_seeds

    async def run(
        self,
        evaluate_fn: Callable[[StrategySeed], Any],
        stop_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[str, int, int], Any] | None = None,
    ) -> list[tuple[StrategySeed, float]]:
        """
        Run evolutionary search.

        Args:
            evaluate_fn: Async callable (seed) -> score (float).
            stop_check: Callable returning True to abort.
            progress_callback: (phase, current, total) callback.

        Returns:
            Final population as list of (seed, score) sorted by score desc.
        """
        population = self._initial_seeds[:self.population_size]
        best_all_time: list[tuple[StrategySeed, float]] = []

        for gen in range(1, self.generations + 1):
            if stop_check and stop_check():
                break

            if progress_callback:
                progress_callback("evolving", gen, self.generations)

            logger.info("Evolution gen %d/%d: evaluating %d seeds",
                        gen, self.generations, len(population))

            # Evaluate all seeds
            scored: list[tuple[StrategySeed, float]] = []
            for i, seed in enumerate(population):
                if stop_check and stop_check():
                    break
                try:
                    score = await evaluate_fn(seed)
                    scored.append((seed, float(score)))
                except Exception as exc:
                    logger.warning("Seed %s eval failed: %s", seed.name, exc)
                    scored.append((seed, -999.0))

            # Track all-time best
            best_all_time.extend(scored)
            best_all_time = sorted(best_all_time, key=lambda x: x[1], reverse=True)
            # Deduplicate by hash, keeping best score
            seen = set()
            deduped = []
            for s, sc in best_all_time:
                if s.seed_hash not in seen:
                    deduped.append((s, sc))
                    seen.add(s.seed_hash)
            best_all_time = deduped[:self.population_size * 2]

            if gen < self.generations:
                # Evolve next generation
                population = evolve_generation(
                    scored,
                    population_size=self.population_size,
                    mutation_rate=self.mutation_rate,
                    crossover_rate=self.crossover_rate,
                    elite_count=self.elite_count,
                )

            if scored:
                top = max(scored, key=lambda x: x[1])
                logger.info(
                    "Gen %d best: %s (score=%.3f)",
                    gen, top[0].name, top[1],
                )

        return sorted(best_all_time, key=lambda x: x[1], reverse=True)
