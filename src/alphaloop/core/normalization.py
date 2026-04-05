"""
core/normalization.py — Centralised price-distance normalization.

Single source of truth for converting raw price deltas into points/pips
and checking SL distance bounds.  Used by:
  - pipeline/construction.py  (trade construction)
  - pipeline/invalidation.py  (safety-net validation)
  - validation/rules.py       (hard-rule validation)
  - risk/sizer.py             (lot-size computation)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DistanceInfo:
    """Normalised distance between two price levels."""

    price_delta: float   # absolute raw price difference
    points: float        # price_delta / pip_size
    pips: float          # same as points (for forex; metals use points)
    atr_multiple: float  # price_delta / atr  (0.0 when ATR unavailable)


def normalize_distance(
    entry: float,
    level: float,
    pip_size: float,
    atr: float | None = None,
) -> DistanceInfo:
    """Convert a raw entry→level gap into a ``DistanceInfo``.

    Parameters
    ----------
    entry : float
        Entry price (or reference level).
    level : float
        Target level (SL, TP, structure boundary, …).
    pip_size : float
        Asset-specific pip/point size (e.g. 0.1 for XAUUSD).
    atr : float | None
        Current ATR value.  When *None*, ``atr_multiple`` is set to 0.0.

    Returns
    -------
    DistanceInfo
        Immutable normalised distance.
    """
    if pip_size <= 0:
        raise ValueError(f"pip_size must be positive, got {pip_size}")

    price_delta = abs(entry - level)
    pts = round(price_delta / pip_size, 2)
    atr_mult = round(price_delta / atr, 4) if atr and atr > 0 else 0.0

    return DistanceInfo(
        price_delta=round(price_delta, 8),
        points=pts,
        pips=pts,
        atr_multiple=atr_mult,
    )


def check_bounds(
    distance: DistanceInfo,
    min_points: float,
    max_points: float,
) -> tuple[bool, str]:
    """Check whether *distance* falls within ``[min_points, max_points]``.

    Returns
    -------
    (ok, reason) : tuple[bool, str]
        *ok* is ``True`` when the distance is in bounds.
        *reason* is a human-readable explanation (empty when ok).
    """
    if distance.points < min_points:
        return False, (
            f"SL distance {distance.points:.1f} pts < min {min_points:.0f}"
        )
    if distance.points > max_points:
        return False, (
            f"SL distance {distance.points:.1f} pts > max {max_points:.0f}"
        )
    return True, ""
