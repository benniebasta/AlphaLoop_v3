"""
ATR-based position sizing with margin caps and volatility adjustments.
"""

import logging
from decimal import Decimal, ROUND_DOWN

from alphaloop.config.assets import get_asset_config, AssetConfig
from alphaloop.core.normalization import normalize_distance
from alphaloop.signals.schema import ValidatedSignal

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Computes lot size based on account balance, risk %, SL distance,
    macro alignment modifier, and ATR volatility regime.
    """

    def __init__(
        self,
        account_balance: float,
        asset: AssetConfig | None = None,
        symbol: str = "XAUUSD",
        *,
        risk_per_trade_pct: float = 0.01,
        risk_per_trade_min: float = 0.005,
        risk_score_threshold: float = 0.85,
        sl_slippage_buffer: float = 1.15,
        leverage: int = 100,
        contract_size: int = 100,
        margin_cap_pct: float = 0.30,
        macro_modifier_abort_threshold: float = 0.3,
    ):
        self.account_balance = account_balance
        self.asset = asset or get_asset_config(symbol)
        self.pip_value_per_lot = self.asset.pip_value_per_lot
        self._existing_margin_usd = 0.0

        # Config
        self.risk_per_trade_pct = risk_per_trade_pct
        self.risk_per_trade_min = risk_per_trade_min
        self.risk_score_threshold = risk_score_threshold
        self.sl_slippage_buffer = sl_slippage_buffer
        self.leverage = leverage
        self.contract_size = contract_size
        self.margin_cap_pct = margin_cap_pct
        self.macro_modifier_abort_threshold = macro_modifier_abort_threshold

    def compute_lot_size(
        self,
        validated_signal: ValidatedSignal,
        macro_modifier: float = 1.0,
        atr_h1: float | None = None,
        rolling_dd_modifier: float = 1.0,
        risk_pct_override: float | None = None,
        confidence: float | None = None,
    ) -> dict:
        """Returns lot size and risk breakdown.

        risk_pct_override: strategy-specific risk_pct from active strategy params.
        confidence: signal confidence for confidence-based sizing (Phase 5C).
        """
        entry = validated_signal.final_entry
        sl = validated_signal.final_sl
        direction = validated_signal.original.direction

        if entry <= 0:
            raise ValueError(f"Invalid entry price {entry}")

        if direction == "BUY" and sl >= entry:
            raise ValueError(f"BUY SL {sl} >= entry {entry}")
        if direction == "SELL" and sl <= entry:
            raise ValueError(f"SELL SL {sl} <= entry {entry}")

        if validated_signal.risk_score > self.risk_score_threshold:
            raise ValueError(
                f"Risk score {validated_signal.risk_score:.2f} > threshold {self.risk_score_threshold}"
            )

        # Centralised distance normalization (core/normalization.py)
        _dist = normalize_distance(entry, sl, self.asset.pip_size)
        sl_distance_points = _dist.points
        if sl_distance_points <= 0:
            raise ValueError("SL distance is zero")

        # Slippage buffer — crypto wider than metals/forex
        buffer = 1.30 if self.asset.asset_class == "crypto" else self.sl_slippage_buffer
        sl_distance_points *= buffer

        # Enforce SL distance guardrails
        sl_distance_points = max(sl_distance_points, self.asset.sl_min_points)
        sl_distance_points = min(sl_distance_points, self.asset.sl_max_points)

        # Base risk — use strategy override if provided
        base_risk = risk_pct_override if risk_pct_override is not None else self.risk_per_trade_pct
        risk_pct = self._adjust_risk(
            base_risk, macro_modifier,
            validated_signal.risk_score, atr_h1,
        )

        # Confidence-based sizing (Phase 5C)
        if confidence is not None:
            if confidence >= 0.85:
                risk_pct *= 1.25
            elif confidence >= 0.70:
                pass  # 1.0× (normal)
            elif confidence >= 0.55:
                risk_pct *= 0.75
            else:
                risk_pct *= 0.50

        if risk_pct == 0.0:
            raise ValueError("risk_pct=0.0 — extreme macro conflict")

        # Vol regime modifier
        if atr_h1 is not None and atr_h1 > 0 and entry > 0:
            atr_pct = (atr_h1 / entry) * 100.0
            vol_mod = self._vol_regime_modifier(atr_pct)
            risk_pct = max(risk_pct * vol_mod, self.risk_per_trade_min)

        # Rolling drawdown modifier
        if rolling_dd_modifier < 1.0:
            risk_pct = max(risk_pct * rolling_dd_modifier, self.risk_per_trade_min)

        # Decimal math for precision
        risk_amount = Decimal(str(self.account_balance)) * Decimal(str(risk_pct))
        sl_cost = Decimal(str(sl_distance_points)) * Decimal(str(self.pip_value_per_lot))
        if sl_cost <= 0:
            raise ValueError("SL cost per lot is zero")

        raw_lots = risk_amount / sl_cost
        lots = float(max(raw_lots.quantize(Decimal("0.01"), rounding=ROUND_DOWN), Decimal("0.01")))

        # Margin cap
        margin_per_lot = (entry * self.contract_size) / max(self.leverage, 1)
        if margin_per_lot > 0:
            remaining_cap = (self.account_balance * self.margin_cap_pct) - self._existing_margin_usd
            if remaining_cap > 0:
                max_lots = remaining_cap / margin_per_lot
            else:
                max_lots = 0.01
            if lots > max_lots:
                lots = float(max(
                    Decimal(str(max_lots)).quantize(Decimal("0.01"), rounding=ROUND_DOWN),
                    Decimal("0.01"),
                ))

        margin_required = round(margin_per_lot * lots, 2)

        return {
            "lots": lots,
            "risk_amount_usd": round(float(risk_amount), 2),
            "risk_pct": round(risk_pct * 100, 3),
            "sl_distance_points": round(sl_distance_points, 1),
            "macro_modifier": macro_modifier,
            "risk_score": validated_signal.risk_score,
            "margin_required": margin_required,
            "leverage": self.leverage,
        }

    @staticmethod
    def _vol_regime_modifier(atr_pct: float) -> float:
        if atr_pct < 0.5:
            return 1.10
        if atr_pct < 1.5:
            return 1.00
        if atr_pct < 2.0:
            return 0.80
        return 0.60

    def _adjust_risk(
        self,
        base: float,
        macro_modifier: float,
        risk_score: float,
        atr_h1: float | None,
    ) -> float:
        adjusted = base * macro_modifier
        if macro_modifier <= self.macro_modifier_abort_threshold:
            return 0.0
        if risk_score > 0.6:
            adjusted *= 1.0 - (risk_score - 0.6) * 0.5
        return max(adjusted, self.risk_per_trade_min)
