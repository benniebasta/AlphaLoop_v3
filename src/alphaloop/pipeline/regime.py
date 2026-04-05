"""
pipeline/regime.py — Stage 2: Regime classification.

Produces a RegimeSnapshot that parameterises every downstream stage.
NEVER blocks.  Dead-market blocking is handled by MarketGate (Stage 1).

Regime determines:
  - which setup types are valid
  - confidence ceiling / min_entry adjustment
  - position size multiplier
  - group-weight overrides
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.pipeline.types import PortfolioContext, RegimeSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regime parameterisation table (initial calibration defaults)
# ---------------------------------------------------------------------------

_REGIME_TABLE: dict[str, dict[str, Any]] = {
    "trending": {
        "allowed_setups": ["pullback", "breakout", "continuation"],
        "confidence_ceiling": 95.0,
        "min_entry_adjustment": -5.0,  # easier to enter in trends
        "size_multiplier": 1.1,
        "weight_overrides": {
            "trend": 0.35,
            "momentum": 0.25,
            "structure": 0.15,
            "volume": 0.10,
            "volatility": 0.15,
        },
    },
    "ranging": {
        "allowed_setups": ["range_bounce", "reversal"],
        "confidence_ceiling": 80.0,
        "min_entry_adjustment": 5.0,  # stricter in chop
        "size_multiplier": 0.8,
        "weight_overrides": {
            "trend": 0.15,
            "momentum": 0.20,
            "structure": 0.35,
            "volume": 0.10,
            "volatility": 0.20,
        },
    },
    "volatile": {
        "allowed_setups": ["pullback", "reversal"],
        "confidence_ceiling": 85.0,
        "min_entry_adjustment": 10.0,  # need higher conviction
        "size_multiplier": 0.6,
        "weight_overrides": {
            "trend": 0.20,
            "momentum": 0.20,
            "structure": 0.25,
            "volume": 0.10,
            "volatility": 0.25,
        },
    },
    "neutral": {
        "allowed_setups": [
            "pullback",
            "breakout",
            "reversal",
            "continuation",
            "range_bounce",
        ],
        "confidence_ceiling": 90.0,
        "min_entry_adjustment": 0.0,
        "size_multiplier": 1.0,
        "weight_overrides": {},  # use defaults
    },
}

# Volatility band thresholds (ATR% of price)
_VOL_COMPRESSED = 0.0015   # < 0.15%
_VOL_NORMAL_MAX = 0.005    # < 0.50%
_VOL_ELEVATED_MAX = 0.01   # < 1.0%
# above 1.0% = extreme


_ALL_REGIMES = ("trending", "ranging", "volatile", "neutral")
_EWM_ALPHA = 0.30   # 30% weight on current candle, 70% on history


class RegimeClassifier:
    """
    Classifies market regime from indicator features and produces
    a RegimeSnapshot consumed by all downstream stages.

    S-03: Applies exponential weighted smoothing (α=0.30) across consecutive
    candle calls so a single anomalous bar cannot flip the regime.  The raw
    one-hot classification is blended into a probability vector; the regime
    with the highest smoothed score wins.

    Persist / restore smoothed state via the ``state`` property and
    ``load_state()`` so regime continuity survives restarts.
    """

    def __init__(
        self,
        *,
        regime_overrides: dict[str, dict] | None = None,
        tools: list | None = None,
        ewm_alpha: float = _EWM_ALPHA,
    ):
        self._table = dict(_REGIME_TABLE)
        if regime_overrides:
            for regime, overrides in regime_overrides.items():
                if regime in self._table:
                    self._table[regime].update(overrides)
        self._tools: list = tools or []
        self._alpha = ewm_alpha
        # Smoothed regime probability vector (sums to 1.0 after first update)
        self._smoothed: dict[str, float] = {r: 0.25 for r in _ALL_REGIMES}
        # Optional async callback for persisting state after each classify()
        self._on_state_changed: Any | None = None

    # ── State persistence ──────────────────────────────────────────────────

    @property
    def state(self) -> dict:
        """Serialisable state dict for cross-restart persistence."""
        return {"smoothed": dict(self._smoothed), "alpha": self._alpha}

    def load_state(self, state: dict) -> None:
        """Restore smoothed scores from a previously saved state dict."""
        loaded = state.get("smoothed", {})
        for r in _ALL_REGIMES:
            if r in loaded:
                self._smoothed[r] = float(loaded[r])
        logger.debug("[Regime] State restored: %s", self._smoothed)

    # ── EWM helpers ─────────────────────────────────────────────────────────

    def _update_smoothed(self, raw_regime: str) -> str:
        """Blend a one-hot raw classification into the smoothed score vector.

        Returns the regime with the highest smoothed score.
        """
        alpha = self._alpha
        for r in _ALL_REGIMES:
            raw_score = 1.0 if r == raw_regime else 0.0
            self._smoothed[r] = alpha * raw_score + (1.0 - alpha) * self._smoothed[r]

        # Renormalise to sum=1.0 (avoids floating-point drift)
        total = sum(self._smoothed.values()) or 1.0
        for r in _ALL_REGIMES:
            self._smoothed[r] = self._smoothed[r] / total

        return max(self._smoothed, key=lambda r: self._smoothed[r])

    async def classify(self, context) -> RegimeSnapshot:
        """Build RegimeSnapshot from market context indicators."""

        indicators = getattr(context, "indicators", {})
        m15 = indicators.get("M15", {})
        h1 = indicators.get("H1", {})

        choppiness = float(m15.get("choppiness", 50.0) or 50.0)
        adx = float(m15.get("adx", 25.0) or 25.0)
        atr_pct = float(h1.get("atr_pct", 0.003) or 0.003)

        # --- Raw regime classification (one-hot) ---
        raw_regime = self._classify_regime(choppiness, adx, atr_pct)

        # --- S-03: Apply EWM smoothing across candles ---
        regime = self._update_smoothed(raw_regime)

        if regime != raw_regime:
            logger.debug(
                "[Regime] Smoothed regime=%s (raw=%s | scores=%s)",
                regime, raw_regime,
                {r: f"{v:.3f}" for r, v in self._smoothed.items()},
            )

        # --- Macro regime ---
        macro_regime = self._classify_macro(context)

        # --- Volatility band ---
        volatility_band = self._classify_volatility_band(atr_pct)

        # --- Session quality ---
        session = getattr(context, "session", None)
        session_quality = 0.5
        if session:
            session_quality = float(getattr(session, "score", 0.5) or 0.5)

        # --- Regime params ---
        params = self._table.get(regime, self._table["neutral"])

        # --- Portfolio context (early exposure awareness) ---
        portfolio_ctx = self._build_portfolio_context(context)

        snapshot = RegimeSnapshot(
            regime=regime,
            macro_regime=macro_regime,
            volatility_band=volatility_band,
            allowed_setups=list(params["allowed_setups"]),
            atr_pct=round(atr_pct, 6),
            choppiness=round(choppiness, 2),
            adx=round(adx, 2),
            session_quality=round(session_quality, 3),
            confidence_ceiling=params["confidence_ceiling"],
            min_entry_adjustment=params["min_entry_adjustment"],
            size_multiplier=params["size_multiplier"],
            weight_overrides=dict(params.get("weight_overrides", {})),
            portfolio_context=portfolio_ctx,
        )

        logger.info(
            "[Regime] %s (raw=%s) | macro=%s | vol=%s | CI=%.1f ADX=%.1f ATR%%=%.4f "
            "| setups=%s | ceiling=%.0f | size=%.2f",
            regime,
            raw_regime,
            macro_regime,
            volatility_band,
            choppiness,
            adx,
            atr_pct,
            params["allowed_setups"],
            params["confidence_ceiling"],
            params["size_multiplier"],
        )

        # Persist smoothed state asynchronously so restarts resume correctly
        if self._on_state_changed is not None:
            try:
                await self._on_state_changed(self.state)
            except Exception as _exc:
                logger.debug("[Regime] State save callback failed: %s", _exc)

        # --- Regime annotation tools (adx_filter, choppiness_index, trendilo) ---
        # Never block.  Results annotate the snapshot for downstream logging.
        for tool in self._tools:
            try:
                tool_result = await tool.timed_run(context)
                logger.debug(
                    "[Regime] tool=%s passed=%s bias=%s reason=%s",
                    tool_result.tool_name,
                    tool_result.passed,
                    tool_result.bias,
                    tool_result.reason,
                )
            except Exception as exc:
                logger.warning("[Regime] Tool %s error: %s", getattr(tool, "name", "?"), exc)

        return snapshot

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_regime(choppiness: float, adx: float, atr_pct: float) -> str:
        """
        Classify market regime.  Dead market (ATR < 0.05%) is handled by
        MarketGate, not here.
        """
        # Ranging: high choppiness OR very low ADX
        if choppiness > 61.8 or adx < 15:
            return "ranging"

        # Trending: low choppiness AND strong ADX
        if choppiness < 38.2 and adx > 25:
            return "trending"

        # Volatile: high ATR%
        if atr_pct > 0.007:
            return "volatile"

        return "neutral"

    @staticmethod
    def _classify_macro(context) -> str:
        """Classify macro regime from DXY + sentiment."""
        dxy = getattr(context, "dxy", None)
        sentiment = getattr(context, "sentiment", None)

        if dxy is None:
            return "neutral"

        dxy_bias = ""
        dxy_strength = 0.0
        if isinstance(dxy, dict):
            dxy_bias = str(dxy.get("bias", "")).lower()
            dxy_strength = float(dxy.get("strength", 0) or 0)
        else:
            dxy_bias = str(getattr(dxy, "bias", "")).lower()
            dxy_strength = float(getattr(dxy, "strength", 0) or 0)

        sentiment_bias = ""
        if sentiment:
            if isinstance(sentiment, dict):
                sentiment_bias = str(sentiment.get("bias", "")).lower()
            else:
                sentiment_bias = str(getattr(sentiment, "bias", "")).lower()

        # Risk-off: strong USD + weak/bearish sentiment
        if "bullish" in dxy_bias and dxy_strength >= 0.30:
            if sentiment_bias in ("bearish", "risk_off", ""):
                return "risk_off"

        # Risk-on: weak USD + bullish sentiment
        if "bearish" in dxy_bias and sentiment_bias in ("bullish", "risk_on"):
            return "risk_on"

        return "neutral"

    @staticmethod
    def _classify_volatility_band(atr_pct: float) -> str:
        if atr_pct < _VOL_COMPRESSED:
            return "compressed"
        if atr_pct < _VOL_NORMAL_MAX:
            return "normal"
        if atr_pct < _VOL_ELEVATED_MAX:
            return "elevated"
        return "extreme"

    @staticmethod
    def _build_portfolio_context(context) -> PortfolioContext:
        """Build early portfolio exposure snapshot."""
        ctx = PortfolioContext()

        symbol = getattr(context, "symbol", "")
        open_trades = getattr(context, "open_trades", None)
        if not open_trades:
            return ctx

        risk_monitor = getattr(context, "risk_monitor", None)

        # Count same-symbol and same-direction exposure
        trades = open_trades if isinstance(open_trades, list) else []
        if isinstance(open_trades, dict):
            trades = list(open_trades.values())

        for trade in trades:
            t_symbol = ""
            if isinstance(trade, dict):
                t_symbol = trade.get("symbol", "")
            else:
                t_symbol = getattr(trade, "symbol", "")

            if t_symbol == symbol:
                ctx.same_symbol_exposure += 1

        # Portfolio heat from risk monitor
        if risk_monitor:
            balance = getattr(risk_monitor, "account_balance", 0)
            open_risk = getattr(risk_monitor, "_open_risk_usd", 0)
            if balance > 0:
                ctx.portfolio_heat_pct = round(open_risk / balance, 4)
                max_heat = getattr(risk_monitor, "max_portfolio_heat_pct", 0.06)
                ctx.risk_budget_remaining_pct = round(
                    max(0.0, max_heat - ctx.portfolio_heat_pct), 4
                )

        return ctx
