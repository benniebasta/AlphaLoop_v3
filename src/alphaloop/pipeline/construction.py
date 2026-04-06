"""
pipeline/construction.py — Stage 3B: Constraint-first trade construction.

Converts a :class:`DirectionHypothesis` into a fully constructed
:class:`CandidateSignal` with structure-derived SL and R:R-derived TP.

Design principles:
  1. SL is derived from market structure (swing lows/highs, FVG boundaries).
  2. ATR fallback (lowest priority) — used only when no structure-derived SL exists.
  3. TP is derived mathematically from SL distance × rr_target.
  4. R:R is valid by construction.
  5. All distance checks use centralised ``core.normalization``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from alphaloop.core.normalization import DistanceInfo, normalize_distance, check_bounds
from alphaloop.core.setup_types import normalize_pipeline_setup_type
from alphaloop.pipeline.types import CandidateSignal, DirectionHypothesis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ConstructionResult:
    """Outcome of a single trade-construction attempt."""

    signal: CandidateSignal | None = None
    sl_source: str = ""
    rejection_reason: str | None = None
    candidates_considered: int = 0
    structural_warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# TradeConstructor
# ---------------------------------------------------------------------------

class TradeConstructor:
    """Construct valid trades from direction hypotheses and market structure.

    Parameters are injected explicitly — never rely on global defaults.

    Args:
        pip_size: Asset-specific pip/point size (e.g. 0.1 for XAUUSD).
        sl_min_pts: Minimum SL distance in points.
        sl_max_pts: Maximum SL distance in points.
        tp1_rr: Risk-reward ratio for TP1.
        tp2_rr: Risk-reward ratio for TP2.
        entry_zone_atr_mult: Half-width of entry zone as ATR multiple.
        sl_buffer_atr: Buffer beyond structure level to avoid sweeps,
                       expressed as ATR multiple (default 0.15).
        sl_atr_mult: ATR multiplier for fallback SL when no structure
                     exists (default 1.5).  Set to 0 to disable.
    """

    def __init__(
        self,
        *,
        pip_size: float,
        sl_min_pts: float,
        sl_max_pts: float,
        tp1_rr: float,
        tp2_rr: float,
        entry_zone_atr_mult: float = 0.25,
        sl_buffer_atr: float = 0.15,
        sl_atr_mult: float = 1.5,
        tools: list | None = None,
    ) -> None:
        self._pip_size = pip_size
        self._sl_min = sl_min_pts
        self._sl_max = sl_max_pts
        self._tp1_rr = tp1_rr
        self._tp2_rr = tp2_rr
        self._zone_mult = entry_zone_atr_mult
        self._sl_buffer_atr = sl_buffer_atr
        self._sl_atr_mult = sl_atr_mult
        self._tools: list = tools or []  # swing_structure, fvg_guard, bos_guard

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def construct(
        self,
        hypothesis: DirectionHypothesis,
        bid: float,
        ask: float,
        indicators: dict,
        atr: float,
    ) -> ConstructionResult:
        """Attempt to construct a valid trade from *hypothesis*.

        Args:
            hypothesis: Direction + confidence from the signal engine.
            bid: Current bid price.
            ask: Current ask price.
            indicators: M15 indicator dict from MarketContext.
            atr: Current M15 ATR value.

        Returns:
            ConstructionResult with ``signal`` populated on success,
            or ``rejection_reason`` explaining why construction failed.
        """
        direction = hypothesis.direction

        # Entry price: BUY = ask (you buy at the ask), SELL = bid
        entry = ask if direction == "BUY" else bid
        zone_half = atr * self._zone_mult
        entry_zone = (
            round(entry - zone_half, 5),
            round(entry + zone_half, 5),
        )

        # --- Derive SL from market structure ---
        sl_result = self._derive_sl(direction, entry, indicators, atr)
        if sl_result is None:
            reason = "no valid SL from market structure"
            logger.info(
                "[construction] %s hypothesis — %s",
                direction, reason,
            )
            return ConstructionResult(
                rejection_reason=reason,
                candidates_considered=self._last_candidates,
            )

        sl_price, sl_source, sl_distance = sl_result

        # --- Derive TP from SL distance ---
        tp_list = self._derive_tp(direction, entry, sl_distance)

        # Compute R:R (guaranteed valid by construction because tp1_rr ≥ 1.0)
        risk = sl_distance.price_delta
        reward_tp1 = abs(tp_list[0] - entry) if tp_list else 0.0
        rr = round(reward_tp1 / risk, 2) if risk > 0 else 0.0

        signal = CandidateSignal(
            direction=direction,
            setup_type=normalize_pipeline_setup_type(hypothesis.setup_tag),
            entry_zone=entry_zone,
            stop_loss=round(sl_price, 5),
            take_profit=[round(tp, 5) for tp in tp_list],
            raw_confidence=hypothesis.confidence,
            rr_ratio=rr,
            signal_sources=hypothesis.source_names.split("+") if hypothesis.source_names else [],
            reasoning=hypothesis.reasoning,
            generated_at=hypothesis.generated_at,
            sl_source=sl_source,
            construction_candidates=self._last_candidates,
        )

        logger.info(
            "[construction] trade constructed: %s SL from %s at %.5f "
            "(distance=%.1f pts, RR=%.2f, candidates=%d)",
            direction, sl_source, sl_price,
            sl_distance.points, rr, self._last_candidates,
        )

        return ConstructionResult(
            signal=signal,
            sl_source=sl_source,
            candidates_considered=self._last_candidates,
        )

    # ------------------------------------------------------------------
    # SL derivation (structure-first, no ATR fallback)
    # ------------------------------------------------------------------

    # Number of candidates evaluated in the last _derive_sl call
    _last_candidates: int = 0

    def _derive_sl(
        self,
        direction: str,
        entry: float,
        indicators: dict,
        atr: float,
    ) -> tuple[float, str, DistanceInfo] | None:
        """Find a structure-derived SL that satisfies distance bounds.

        Tries candidates in priority order; returns the first one that
        passes ``check_bounds``.  Returns *None* when no valid
        candidate exists — the caller must NOT emit a trade.
        """
        buffer = self._sl_buffer_atr * atr if atr > 0 else 0.0
        candidates: list[tuple[float, str]] = []

        if direction == "BUY":
            candidates = self._buy_candidates(entry, indicators, buffer, atr)
        else:
            candidates = self._sell_candidates(entry, indicators, buffer, atr)

        self._last_candidates = len(candidates)

        for raw_price, source in candidates:
            dist = normalize_distance(entry, raw_price, self._pip_size, atr)
            ok, reason = check_bounds(dist, self._sl_min, self._sl_max)
            if ok:
                return raw_price, source, dist
            else:
                logger.debug(
                    "[construction] SL candidate '%s' at %.5f rejected: %s",
                    source, raw_price, reason,
                )

        return None

    # ------------------------------------------------------------------
    # Candidate builders
    # ------------------------------------------------------------------

    def _buy_candidates(
        self,
        entry: float,
        indicators: dict,
        buffer: float,
        atr: float,
    ) -> list[tuple[float, str]]:
        """SL candidates for a BUY trade (levels below entry)."""
        candidates: list[tuple[float, str]] = []

        # 1. Nearest swing low below entry
        swing_lows = indicators.get("swing_lows") or []
        below = [
            s for s in swing_lows
            if isinstance(s, dict) and s.get("price") is not None
            and float(s["price"]) < entry
        ]
        if below:
            # Take the nearest (highest price) swing low below entry
            nearest = max(below, key=lambda s: float(s["price"]))
            candidates.append((
                float(nearest["price"]) - buffer,
                "swing_low",
            ))

        # 2. Bullish FVG bottom
        fvg_data = indicators.get("fvg") or {}
        bullish_fvgs = fvg_data.get("bullish") or []
        for fvg in reversed(bullish_fvgs):  # most recent first
            bottom = fvg.get("bottom")
            if bottom is not None and float(bottom) < entry:
                candidates.append((
                    float(bottom) - buffer,
                    "fvg_bottom",
                ))
                break  # only use most recent qualifying FVG

        # 3. ATR fallback (lowest priority)
        if atr > 0 and self._sl_atr_mult > 0:
            candidates.append((
                entry - (atr * self._sl_atr_mult) - buffer,
                "atr_fallback",
            ))

        return candidates

    def _sell_candidates(
        self,
        entry: float,
        indicators: dict,
        buffer: float,
        atr: float,
    ) -> list[tuple[float, str]]:
        """SL candidates for a SELL trade (levels above entry)."""
        candidates: list[tuple[float, str]] = []

        # 1. Nearest swing high above entry
        swing_highs = indicators.get("swing_highs") or []
        above = [
            s for s in swing_highs
            if isinstance(s, dict) and s.get("price") is not None
            and float(s["price"]) > entry
        ]
        if above:
            nearest = min(above, key=lambda s: float(s["price"]))
            candidates.append((
                float(nearest["price"]) + buffer,
                "swing_high",
            ))

        # 2. Bearish FVG top
        fvg_data = indicators.get("fvg") or {}
        bearish_fvgs = fvg_data.get("bearish") or []
        for fvg in reversed(bearish_fvgs):
            top = fvg.get("top")
            if top is not None and float(top) > entry:
                candidates.append((
                    float(top) + buffer,
                    "fvg_top",
                ))
                break

        # 3. ATR fallback (lowest priority)
        if atr > 0 and self._sl_atr_mult > 0:
            candidates.append((
                entry + (atr * self._sl_atr_mult) + buffer,
                "atr_fallback",
            ))

        return candidates

    # ------------------------------------------------------------------
    # TP derivation
    # ------------------------------------------------------------------

    def _derive_tp(
        self,
        direction: str,
        entry: float,
        sl_distance: DistanceInfo,
    ) -> list[float]:
        """Compute TP1 and TP2 from SL distance × rr_target."""
        delta = sl_distance.price_delta
        if direction == "BUY":
            tp1 = entry + delta * self._tp1_rr
            tp2 = entry + delta * self._tp2_rr
        else:
            tp1 = entry - delta * self._tp1_rr
            tp2 = entry - delta * self._tp2_rr
        return [round(tp1, 5), round(tp2, 5)]
