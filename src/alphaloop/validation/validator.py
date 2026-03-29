"""
Async universal signal validator.
Two-stage: hard rules (instant) then optional AI validation.
"""

import json
import logging
import re
from typing import Optional

from alphaloop.config.assets import get_asset_config, AssetConfig
from alphaloop.core.types import ValidationStatus
from alphaloop.signals.schema import TradeSignal, ValidatedSignal, RejectionFeedback
from alphaloop.validation.rules import HardRuleChecker
from alphaloop.validation.prompts import build_validator_system_prompt, build_validation_prompt

logger = logging.getLogger(__name__)

# Map hard rule failure strings to structured feedback
_RULE_MAP = {
    "confidence": ("low_confidence", "min_confidence", "Increase signal confidence"),
    "sl_tp_dir": ("invalid_sl_tp", "stop_loss", "Ensure SL/TP direction matches trade direction"),
    "sl_distance": ("sl_distance", "sl_atr_mult", "Adjust SL distance within ATR range"),
    "rr_ratio": ("low_rr", "tp1_rr", "Improve R:R ratio"),
    "session": ("bad_session", "min_session_score", "Wait for a higher-quality session"),
    "spread": ("wide_spread", "min_spread_points", "Wait for spread to narrow"),
    "rsi_extreme": ("rsi_extreme", "rsi_overbought", "Wait for RSI to normalize"),
    "ema200_trend": ("trend_conflict", "ema_trend", "Trade with H1 EMA200 trend"),
    "news": ("news_blackout", "news_pre_minutes", "Wait for news window to clear"),
}


def _parse_rejection(failure_str: str) -> RejectionFeedback:
    fl = failure_str.lower()
    for key, (code, param, suggestion) in _RULE_MAP.items():
        if key in fl:
            return RejectionFeedback(
                reason_code=code,
                parameter_violated=param,
                suggested_adjustment=suggestion,
                severity="high",
            )
    return RejectionFeedback(
        reason_code="unknown",
        parameter_violated="",
        suggested_adjustment=failure_str,
        severity="medium",
    )


class UniversalValidator:
    """
    Validates trade signals using any AI model.
    Stage 1: Hard rules (instant, no API cost)
    Stage 2: AI reasoning validation (async)
    """

    def __init__(self, symbol: str = "XAUUSD", *, dry_run: bool = True):
        self.asset = get_asset_config(symbol)
        self.hard_rules = HardRuleChecker(symbol=symbol)
        self.dry_run = dry_run
        logger.info(
            "UniversalValidator initialised: %s (%s)",
            self.asset.display_name,
            self.asset.symbol,
        )

    async def validate(
        self,
        signal: TradeSignal,
        market_context: dict,
        *,
        validation_cfg: dict | None = None,
        validation_overrides: dict | None = None,
        ai_caller=None,
    ) -> ValidatedSignal:
        """
        Full validation pipeline.
        ai_caller: async callable(model_id, messages, **kwargs) -> str
        validation_overrides: strategy-specific threshold overrides (min_confidence, min_rr, etc.)
        """
        c = validation_cfg or {}
        # Merge strategy overrides into validation config
        if validation_overrides:
            c = {**c, **validation_overrides}

        # Stage 1: Hard rules
        hard_failures = self.hard_rules.check(signal, market_context, cfg=c)
        if hard_failures:
            logger.info("[validator] Hard rule rejection: %s", hard_failures)
            feedback = [_parse_rejection(f) for f in hard_failures]
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.REJECTED,
                rejection_reasons=hard_failures,
                rejection_feedback=feedback,
                risk_score=0.9,
                validator_reasoning="Failed hard rule pre-checks",
            )

        # Stage 2: Resolve validator model
        model_id = c.get("validator_model", "")
        if not model_id and ai_caller is None:
            if not self.dry_run:
                logger.critical("[validator] No validator model in LIVE mode — REJECTING")
                return ValidatedSignal(
                    original=signal,
                    status=ValidationStatus.REJECTED,
                    rejection_reasons=["No validator model configured for live trading"],
                    risk_score=0.95,
                    validator_reasoning="AI validation required in live mode",
                )
            logger.warning("[validator] No validator model — auto-approving (dry-run)")
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.APPROVED,
                rejection_reasons=[],
                risk_score=0.3,
                validator_reasoning="No validator model; hard rules passed (dry-run)",
            )

        if ai_caller is None:
            logger.warning("[validator] No AI caller provided — auto-approving")
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.APPROVED,
                rejection_reasons=[],
                risk_score=0.3,
                validator_reasoning="AI caller not available; hard rules passed",
            )

        # Stage 3: AI validation
        system_prompt = build_validator_system_prompt(
            self.asset,
            min_rr=c.get("min_rr", 1.5),
            min_confidence=c.get("min_confidence", 0.70),
        )
        user_prompt = build_validation_prompt(signal, market_context, self.asset)

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            raw = await ai_caller(model_id, messages, max_tokens=600)
        except Exception as e:
            logger.error("[validator] AI call failed: %s", e)
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.REJECTED,
                rejection_reasons=["Validator unavailable"],
                risk_score=0.8,
                validator_reasoning=f"API call failed: {e}",
            )

        if not raw:
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.REJECTED,
                rejection_reasons=["Validator returned empty response"],
                risk_score=0.8,
                validator_reasoning="Empty API response",
            )

        return self._parse(signal, raw)

    def _parse(self, signal: TradeSignal, raw_text: str) -> ValidatedSignal:
        """Parse AI response JSON into a ValidatedSignal."""
        clean = re.sub(r"```(?:json)?\s*", "", raw_text).strip()
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            logger.error("[validator] No JSON in response: %s", raw_text[:200])
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.REJECTED,
                rejection_reasons=["Could not parse validator response"],
                risk_score=0.9,
                validator_reasoning=raw_text[:500],
            )
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return ValidatedSignal(
                original=signal,
                status=ValidationStatus.REJECTED,
                rejection_reasons=["JSON decode error"],
                risk_score=0.9,
            )

        status = (
            ValidationStatus.APPROVED
            if data.get("status") == "APPROVED"
            else ValidationStatus.REJECTED
        )

        try:
            risk_score = max(0.0, min(1.0, float(data.get("risk_score", 0.5))))
        except (ValueError, TypeError):
            risk_score = 0.7

        result = ValidatedSignal(
            original=signal,
            status=status,
            rejection_reasons=data.get("rejection_reasons", []),
            adjusted_entry=data.get("adjusted_entry"),
            adjusted_sl=data.get("adjusted_sl"),
            adjusted_tp=data.get("adjusted_tp"),
            risk_score=risk_score,
            validator_reasoning=data.get("reasoning", ""),
        )

        # Re-validate AI-adjusted levels
        if status == ValidationStatus.APPROVED and (
            result.adjusted_sl is not None
            or result.adjusted_entry is not None
        ):
            adj_entry = result.final_entry
            adj_sl = result.final_sl
            direction = signal.direction
            bad = False
            if direction == "BUY" and adj_sl >= adj_entry:
                bad = True
            elif direction == "SELL" and adj_sl <= adj_entry:
                bad = True
            elif adj_sl is not None and adj_sl <= 0:
                bad = True
            if bad:
                logger.warning("[validator] AI-adjusted levels invalid — rejecting")
                result = ValidatedSignal(
                    original=signal,
                    status=ValidationStatus.REJECTED,
                    rejection_reasons=["AI-adjusted SL on wrong side of entry"],
                    risk_score=0.9,
                    validator_reasoning="Adjusted levels failed safety re-check",
                )

        return result
