"""
Correlation guard — cross-asset correlation check.

Blocks or reduces position size when a correlated instrument is already
open in the same direction, preventing duplicate exposure.
"""

from __future__ import annotations

import json
import logging

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult

logger = logging.getLogger(__name__)

# Static fallback correlation map — used if no DB-computed matrix is available
_STATIC_CORRELATIONS: dict[tuple[str, str], float] = {
    ("BTCUSD", "ETHUSD"): 0.92,
    ("BTCUSD", "XAUUSD"): 0.45,
    ("EURUSD", "GBPUSD"): 0.80,
    ("EURUSD", "XAUUSD"): 0.55,
    ("GBPUSD", "XAUUSD"): 0.60,
    ("DXY", "XAUUSD"): -0.75,
    ("DXY", "EURUSD"): -0.85,
    ("BTCUSD", "DXY"): -0.40,
}

# In-memory cache of the dynamically computed matrix (loaded once per process)
_DYNAMIC_MATRIX: dict[tuple[str, str], float] | None = None

_BLOCK_THRESHOLD = 0.90
_REDUCE_THRESHOLD = 0.75
_REDUCE_MODIFIER = 0.50
_UNKNOWN_PAIR_DEFAULT = 0.5
_SETTINGS_KEY = "correlation_matrix"


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    a = a.upper().rstrip("Mm")
    b = b.upper().rstrip("Mm")
    return (a, b) if a <= b else (b, a)


def _parse_dynamic_matrix(raw_json: str) -> dict[tuple[str, str], float]:
    """Parse the stored JSON correlation matrix into a tuple-keyed dict."""
    result: dict[tuple[str, str], float] = {}
    try:
        data: dict[str, float] = json.loads(raw_json)
        for key_str, val in data.items():
            parts = key_str.split("|")
            if len(parts) == 2:
                result[tuple(parts)] = float(val)  # type: ignore[assignment]
    except Exception as e:
        logger.debug("[corr_guard] Failed to parse dynamic matrix: %s", e)
    return result


async def _load_dynamic_matrix_from_settings(settings_service) -> None:
    """Load the dynamic correlation matrix from DB settings (once per process)."""
    global _DYNAMIC_MATRIX
    try:
        raw = await settings_service.get(_SETTINGS_KEY)
        if raw:
            _DYNAMIC_MATRIX = _parse_dynamic_matrix(raw)
            logger.debug("[corr_guard] Loaded dynamic matrix: %d pairs", len(_DYNAMIC_MATRIX))
    except Exception as e:
        logger.debug("[corr_guard] Could not load dynamic matrix: %s", e)


def _get_correlation(sym_a: str, sym_b: str) -> float:
    """Look up correlation — dynamic matrix first, static fallback second."""
    key = _normalize_pair(sym_a, sym_b)
    if key[0] == key[1]:
        return 1.0

    # Try dynamic matrix
    if _DYNAMIC_MATRIX:
        val = _DYNAMIC_MATRIX.get(key)
        if val is not None:
            return val

    # Fall back to static map
    return _STATIC_CORRELATIONS.get(key, _UNKNOWN_PAIR_DEFAULT)


class CorrelationGuard(BaseTool):
    """
    Cross-asset correlation guard.

    Checks open trades for correlated exposures. Blocks if correlation
    >= 0.90 in same direction; reduces size if >= 0.75.
    """

    name = "correlation_guard"
    description = "Cross-asset correlation check — prevents duplicate exposure"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        symbol = context.symbol.upper().rstrip("Mm")
        direction = context.trade_direction.upper()
        open_trades: dict = context.open_trades

        if not open_trades:
            return ToolResult(
                passed=True,
                reason="No open trades — no correlation risk",
            )

        max_corr = 0.0
        max_corr_sym = ""
        max_corr_dir = ""

        for _ticket, info in open_trades.items():
            order = info.get("order_result") if isinstance(info, dict) else None
            if order is None:
                continue
            open_sym = (getattr(order, "symbol", "") or "").upper().rstrip("Mm")
            open_dir = (getattr(order, "direction", "") or "").upper()
            if open_sym == symbol:
                continue  # same symbol handled by max_concurrent_trades

            corr = _get_correlation(symbol, open_sym)
            if abs(corr) > abs(max_corr):
                max_corr = corr
                max_corr_sym = open_sym
                max_corr_dir = open_dir

        if not max_corr_sym:
            return ToolResult(
                passed=True,
                reason="No correlated open positions found",
                data={"open_count": len(open_trades)},
            )

        # Determine effective correlation from direction relationship
        same_direction = max_corr_dir == direction
        effective_corr = abs(max_corr) if (
            (max_corr >= 0 and same_direction) or (max_corr < 0 and not same_direction)
        ) else 0.0

        meta = {
            "correlated_sym": max_corr_sym,
            "correlated_dir": max_corr_dir,
            "raw_correlation": round(max_corr, 2),
            "effective_exposure": round(effective_corr, 2),
            "same_direction": same_direction,
        }

        if effective_corr >= _BLOCK_THRESHOLD:
            return ToolResult(
                passed=False,
                reason=(
                    f"Correlation block: {symbol} {direction} correlated "
                    f"{effective_corr:.0%} with open {max_corr_sym} {max_corr_dir}"
                ),
                severity="block",
                size_modifier=0.0,
                data=meta,
            )

        if effective_corr >= _REDUCE_THRESHOLD:
            return ToolResult(
                passed=True,
                reason=(
                    f"Correlation reduce ({effective_corr:.0%} with {max_corr_sym}): "
                    f"size -> {_REDUCE_MODIFIER:.0%}"
                ),
                size_modifier=_REDUCE_MODIFIER,
                data=meta,
            )

        return ToolResult(
            passed=True,
            reason=f"Correlation OK ({effective_corr:.0%} with {max_corr_sym})",
            data=meta,
        )

    async def extract_features(self, context) -> FeatureResult:
        symbol = context.symbol.upper().rstrip("Mm")
        open_trades: dict = context.open_trades

        max_effective_corr = 0.0

        for _ticket, info in (open_trades or {}).items():
            order = info.get("order_result") if isinstance(info, dict) else None
            if order is None:
                continue
            open_sym = (getattr(order, "symbol", "") or "").upper().rstrip("Mm")
            open_dir = (getattr(order, "direction", "") or "").upper()
            if open_sym == symbol:
                continue

            corr = _get_correlation(symbol, open_sym)
            # Use worst-case (same direction = additive exposure)
            effective = abs(corr)
            if effective > max_effective_corr:
                max_effective_corr = effective

        # correlation_freedom: 100 = no correlated exposure, 0 = fully correlated
        correlation_freedom = round((1.0 - max_effective_corr) * 100, 1)

        return FeatureResult(
            group="trend",
            features={"correlation_freedom": correlation_freedom},
            meta={
                "max_effective_corr": round(max_effective_corr, 2),
                "open_trade_count": len(open_trades) if open_trades else 0,
            },
        )
