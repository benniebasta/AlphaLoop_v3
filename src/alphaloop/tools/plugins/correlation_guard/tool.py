"""
Correlation guard — cross-asset correlation check.

Blocks or reduces position size when a correlated instrument is already
open in the same direction, preventing duplicate exposure.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult

# Static correlation map — (sym_a, sym_b) -> float [0, 1] or negative
_CORRELATIONS: dict[tuple[str, str], float] = {
    ("BTCUSD", "ETHUSD"): 0.92,
    ("BTCUSD", "XAUUSD"): 0.45,
    ("EURUSD", "GBPUSD"): 0.80,
    ("EURUSD", "XAUUSD"): 0.55,
    ("GBPUSD", "XAUUSD"): 0.60,
    ("DXY", "XAUUSD"): -0.75,
    ("DXY", "EURUSD"): -0.85,
    ("BTCUSD", "DXY"): -0.40,
}

_BLOCK_THRESHOLD = 0.90
_REDUCE_THRESHOLD = 0.75
_REDUCE_MODIFIER = 0.50
_UNKNOWN_PAIR_DEFAULT = 0.5


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    a = a.upper().rstrip("Mm")
    b = b.upper().rstrip("Mm")
    return (a, b) if a <= b else (b, a)


def _get_correlation(sym_a: str, sym_b: str) -> float:
    key = _normalize_pair(sym_a, sym_b)
    if key[0] == key[1]:
        return 1.0
    return _CORRELATIONS.get(key, _UNKNOWN_PAIR_DEFAULT)


class CorrelationGuard(BaseTool):
    """
    Cross-asset correlation guard.

    Checks open trades for correlated exposures. Blocks if correlation
    >= 0.90 in same direction; reduces size if >= 0.75.
    """

    name = "correlation_guard"
    description = "Cross-asset correlation check — prevents duplicate exposure"

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
