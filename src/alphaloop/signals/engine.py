"""
Async multi-asset signal engine.
Generates trade signals using configured AI models.
"""

import json
import logging
import re
from typing import Any

from alphaloop.config.assets import get_asset_config, AssetConfig
from alphaloop.signals.schema import TradeSignal

logger = logging.getLogger(__name__)


def _build_signal_system_prompt(asset: AssetConfig) -> str:
    """Build the signal generation system prompt."""
    return f"""You are a professional {asset.display_name} ({asset.symbol}) technical analyst.
Your job is to analyse market data and generate trade signals for {asset.asset_class} markets.

Asset context: {asset.ai_context}

You MUST respond with ONLY valid JSON:
{{
  "trend": "bullish" or "bearish" or "neutral",
  "setup": "pullback" or "breakout" or "reversal" or "continuation" or "range_bounce",
  "entry_zone": [lower_price, upper_price],
  "stop_loss": price,
  "take_profit": [tp1, tp2],
  "confidence": 0.0-1.0,
  "reasoning": "Concise explanation citing specific levels"
}}

Rules:
- Entry zone must be a valid [low, high] range
- SL must be on the loss side of entry for the given trend direction
- TP must be on the profit side
- If no clear setup exists, return {{"trend": "neutral", "confidence": 0.0, "reasoning": "No setup"}}
- Be conservative. Only signal when you see genuine edge."""


def _build_signal_user_prompt(
    asset: AssetConfig,
    context: dict,
) -> str:
    """Build the user prompt with market context."""
    h1 = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
    m15 = context.get("timeframes", {}).get("M15", {}).get("indicators", {})
    session = context.get("session", {})
    price = context.get("current_price", {})

    return f"""=== {asset.symbol} SIGNAL ANALYSIS REQUEST ===

CURRENT PRICE:
  Bid: {price.get('bid')} | Ask: {price.get('ask')} | Spread: {price.get('spread')}

H1 INDICATORS:
  EMA21: {h1.get('ema21')} | EMA55: {h1.get('ema55')} | EMA200: {h1.get('ema200')}
  RSI(14): {h1.get('rsi')}
  ATR(14): {h1.get('atr')}
  Trend Bias: {h1.get('trend_bias')}

M15 INDICATORS:
  EMA21: {m15.get('ema21')} | EMA55: {m15.get('ema55')}
  RSI(14): {m15.get('rsi')}
  BOS: {m15.get('bos')}
  FVG: {m15.get('fvg')}

SESSION: {session.get('name')} (score: {session.get('score')})
DXY: {context.get('dxy', {}).get('value', 'N/A')}
SENTIMENT: {context.get('macro_sentiment', {}).get('bias', 'N/A')}

Analyse and generate a signal now."""


class MultiAssetSignalEngine:
    """
    Generates trade signals using AI models.
    Async — uses an AI caller callback.
    """

    def __init__(self, symbol: str = "XAUUSD"):
        self.asset = get_asset_config(symbol)
        self.symbol = self.asset.symbol

    async def generate_signal(
        self,
        context: dict,
        *,
        ai_caller=None,
        model_id: str = "",
        strategy_params: dict | None = None,
    ) -> TradeSignal | None:
        """
        Generate a trade signal from market context.
        ai_caller: async callable(model_id, messages, **kwargs) -> str
        strategy_params: optional strategy-specific params for prompt building
        Returns None if no signal or neutral.
        """
        if ai_caller is None:
            logger.warning("[signal-engine] No AI caller provided")
            return None

        system_prompt = _build_signal_system_prompt(self.asset)
        user_prompt = _build_signal_user_prompt(self.asset, context)

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            raw = await ai_caller(model_id, messages, max_tokens=800)
        except Exception as e:
            logger.error("[signal-engine] AI call failed: %s", e)
            return None

        if not raw:
            return None

        return self._parse_signal(raw)

    def _parse_signal(self, raw_text: str) -> TradeSignal | None:
        """Parse AI response into a TradeSignal."""
        clean = re.sub(r"```(?:json)?\s*", "", raw_text).strip()
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            logger.error("[signal-engine] No JSON in response: %s", raw_text[:200])
            return None

        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as e:
            logger.error("[signal-engine] JSON parse error: %s", e)
            return None

        # Skip neutral signals
        if data.get("trend") == "neutral" or data.get("confidence", 0) < 0.1:
            logger.info("[signal-engine] Neutral signal — skipping")
            return None

        try:
            signal = TradeSignal(**data)
            logger.info(
                "[signal-engine] %s signal: %s conf=%.2f RR=%.2f",
                self.symbol,
                signal.direction,
                signal.confidence,
                signal.rr_ratio_tp1 or 0,
            )
            return signal
        except Exception as e:
            logger.error("[signal-engine] Signal validation failed: %s", e)
            return None
