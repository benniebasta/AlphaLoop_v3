"""
RSI feature — dual-mode: gate (ALGO_ONLY) or feature (ALGO_AI).

Extracts RSI confirmation logic previously embedded in AlgorithmicSignalEngine.
Provides RSI level, zone analysis, and slope features.
"""

from __future__ import annotations

from alphaloop.tools.base import BaseTool, ToolResult, FeatureResult


class RSIFeature(BaseTool):
    """
    RSI feature / filter plugin.

    Gate mode: blocks BUY if RSI > 75 (overbought), SELL if RSI < 25 (oversold).
    Feature mode: outputs rsi_level, rsi_zone, and directional score.
    """

    name = "rsi_feature"
    description = "RSI momentum feature — validates overbought/oversold conditions"
    requires_direction = True

    async def run(self, context) -> ToolResult:
        rsi_ob = self.config.get("rsi_overbought", 75.0)
        rsi_os = self.config.get("rsi_oversold", 25.0)

        direction = context.trade_direction.upper()
        m15 = context.indicators.get("M15", {})
        rsi_val = m15.get("rsi")

        if rsi_val is None:
            return ToolResult(
                passed=True,
                reason="RSI unavailable — skipping",
                severity="info",
            )

        if direction == "BUY" and rsi_val > rsi_ob:
            return ToolResult(
                passed=False,
                reason=f"BUY blocked: RSI {rsi_val:.1f} > {rsi_ob} — overbought",
                severity="warn",
                data={"rsi": rsi_val},
            )

        if direction == "SELL" and rsi_val < rsi_os:
            return ToolResult(
                passed=False,
                reason=f"SELL blocked: RSI {rsi_val:.1f} < {rsi_os} — oversold",
                severity="warn",
                data={"rsi": rsi_val},
            )

        return ToolResult(
            passed=True,
            reason=f"RSI {rsi_val:.1f} — room for {direction.lower()} entry",
            data={"rsi": rsi_val},
        )

    async def extract_features(self, context) -> FeatureResult:
        rsi_ob = self.config.get("rsi_overbought", 75.0)
        rsi_os = self.config.get("rsi_oversold", 25.0)

        m15 = context.indicators.get("M15", {})
        rsi_val = m15.get("rsi")

        if rsi_val is None:
            return FeatureResult(
                group="momentum",
                features={"rsi_quality": 50.0},
                meta={"status": "unavailable"},
            )

        rsi = float(rsi_val)
        direction = getattr(context, "trade_direction", "")
        if direction:
            direction = direction.upper()

        if not direction:
            rsi_quality = 50.0
        elif direction == "BUY":
            rsi_quality = self._score_buy(rsi)
        else:  # SELL
            rsi_quality = self._score_sell(rsi)

        return FeatureResult(
            group="momentum",
            features={"rsi_quality": round(min(100.0, max(0.0, rsi_quality)), 1)},
            reference_thresholds={"overbought": rsi_ob, "oversold": rsi_os},
            meta={"rsi": rsi_val, "direction": direction or "none"},
        )

    @staticmethod
    def _score_buy(rsi: float) -> float:
        """Direction-aware RSI quality for BUY.

        Sweet spot: RSI 30-60 (room to run up) → 80-100.
        Danger zone: RSI > 70 (overbought) → rapidly declining toward 0.
        """
        if rsi <= 30:
            # Oversold — neutral for buy (could bounce or keep falling)
            return 50.0
        if rsi <= 65:
            # Sweet spot: linear 80→100 across [30, 50], then 100→80 across [50, 65]
            if rsi <= 50:
                return 80.0 + (rsi - 30.0) / 20.0 * 20.0  # 80 → 100
            return 100.0 - (rsi - 50.0) / 15.0 * 20.0  # 100 → 80
        if rsi <= 75:
            # Getting overbought: 80 → 25
            return 80.0 - (rsi - 65.0) / 10.0 * 55.0  # 80 → 25
        # Overbought: 25 → 0
        return max(0.0, 25.0 - (rsi - 75.0) / 25.0 * 25.0)

    @staticmethod
    def _score_sell(rsi: float) -> float:
        """Mirror of _score_buy for SELL direction."""
        # Mirror around RSI=50: sell_score(rsi) = buy_score(100 - rsi)
        return RSIFeature._score_buy(100.0 - rsi)
