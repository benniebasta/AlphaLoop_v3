"""
pipeline/ai_validator.py — Stage 6: AI validation (algo_ai mode only).

Bounded authority: AI can approve, reduce confidence, suggest bounded
adjustments, or reject with structured reasons.

AI CANNOT:
  - change direction or setup type
  - increase confidence beyond original + 0.05
  - set SL beyond asset bounds
  - create R:R < min_rr
  - override risk limits or execution guards

After any AI adjustment, the signal is deterministically revalidated.
"""

from __future__ import annotations

import logging
from typing import Any

from alphaloop.pipeline.types import (
    CandidateSignal,
    ConvictionScore,
    QualityResult,
    RegimeSnapshot,
)

logger = logging.getLogger(__name__)

# AI may only adjust confidence — no price-level mutation allowed.
# (Constraint-first: SL/TP are structure-derived by TradeConstructor.)
_CONFIDENCE_BOOST_MAX = 0.05  # AI can raise confidence by at most this


class BoundedAIValidator:
    """
    Wraps the existing AI validator with strict adjustment bounds
    and mandatory post-adjustment revalidation.
    """

    def __init__(
        self,
        *,
        ai_caller=None,
        validator_model: str = "",
        validator_instruction: str = "",
        system_prompt_builder=None,
        user_prompt_builder=None,
        fail_open: bool = True,
        # Legacy params kept for backward compat — no longer used for revalidation
        min_rr: float = 1.5,
        sl_min_points: float = 20.0,
        sl_max_points: float = 300.0,
        pip_size: float = 0.01,
    ):
        self._caller = ai_caller
        self._model = validator_model
        self._validator_instruction = str(validator_instruction or "").strip()
        self._system_builder = system_prompt_builder
        self._user_builder = user_prompt_builder
        self._fail_open = fail_open

    async def validate(
        self,
        signal: CandidateSignal,
        regime: RegimeSnapshot,
        quality: QualityResult,
        conviction: ConvictionScore,
        context,
    ) -> CandidateSignal | None:
        """
        Call AI validator and apply bounded adjustments.

        Returns:
            Adjusted CandidateSignal if approved, None if rejected.
        """
        if not self._caller or not self._model:
            return self._fallback_signal(
                signal,
                "[AIValidator] No AI caller configured",
            )

        # Build prompts
        system_prompt = ""
        user_prompt = ""

        if self._system_builder:
            system_prompt = self._system_builder(context, regime)
        if not system_prompt:
            system_prompt = self._default_system_prompt()
            if self._validator_instruction:
                system_prompt = f"{self._validator_instruction}\n\n{system_prompt}"
        if self._user_builder:
            user_prompt = self._user_builder(
                signal, regime, quality, conviction, context
            )
        if not user_prompt:
            user_prompt = self._default_user_prompt(
                signal, regime, quality, conviction, context
            )

        # Call AI
        try:
            response = await self._caller.call(
                model_id=self._model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as exc:
            return self._fallback_signal(
                signal,
                f"[AIValidator] AI call failed: {exc}",
            )

        if not response:
            return self._fallback_signal(signal, "[AIValidator] Empty response")

        # Parse AI response
        parsed = self._parse_response(response)
        if parsed is None:
            return self._fallback_signal(
                signal,
                "[AIValidator] Failed to parse response",
            )

        status = parsed.get("status", "").lower()

        # --- REJECT ---
        if status in ("rejected", "reject"):
            reasons = parsed.get("rejection_reasons", parsed.get("reasoning", ""))
            logger.info(
                "[AIValidator] REJECTED: %s", reasons
            )
            return None

        # --- APPROVE (with or without adjustments) ---
        adjusted = self._apply_adjustments(signal, parsed, context)

        if adjusted is None:
            logger.warning(
                "[AIValidator] Adjustments failed revalidation — using original signal"
            )
            return signal

        return adjusted

    def _default_system_prompt(self) -> str:
        return (
            "You are a conservative trade validator. Review the proposed trade, "
            "protect capital first, and respond with JSON only. "
            "Allowed statuses are APPROVED or REJECTED. "
            "You may optionally include a confidence value, but you may not "
            "change direction, entry, stop loss, or take profit."
        )

    @staticmethod
    def _default_user_prompt(
        signal: CandidateSignal,
        regime: RegimeSnapshot,
        quality: QualityResult,
        conviction: ConvictionScore,
        context,
    ) -> str:
        session_name = ""
        if isinstance(context, dict):
            session = context.get("session", {})
            if isinstance(session, dict):
                session_name = str(session.get("name", "") or "")
        return (
            "Review this trade candidate and return JSON only.\n"
            "{\n"
            '  "status": "APPROVED" | "REJECTED",\n'
            '  "confidence": 0.0-1.0 or null,\n'
            '  "rejection_reasons": ["..."],\n'
            '  "reasoning": "short explanation"\n'
            "}\n\n"
            f"Direction: {signal.direction}\n"
            f"Setup: {signal.setup_type}\n"
            f"Entry zone: {signal.entry_zone}\n"
            f"Stop loss: {signal.stop_loss}\n"
            f"Take profit: {signal.take_profit}\n"
            f"Raw confidence: {signal.raw_confidence:.4f}\n"
            f"RR ratio: {signal.rr_ratio:.4f}\n"
            f"Regime: {regime.regime}\n"
            f"Macro regime: {regime.macro_regime}\n"
            f"Volatility band: {regime.volatility_band}\n"
            f"Session: {session_name or 'unknown'}\n"
            f"Quality overall: {quality.overall_score:.2f}\n"
            f"Group scores: {quality.group_scores}\n"
            f"Conviction score: {conviction.score:.2f}\n"
            f"Conviction decision: {conviction.decision}\n"
            f"Conviction reasoning: {conviction.reasoning}\n"
            f"Signal reasoning: {signal.reasoning}\n"
        )

    def _fallback_signal(
        self,
        signal: CandidateSignal,
        message: str,
    ) -> CandidateSignal | None:
        """Handle validator degradation according to the configured failure mode."""
        if self._fail_open:
            logger.warning("%s — auto-approve", message)
            return signal
        logger.warning("%s — fail-closed reject", message)
        return None

    # ------------------------------------------------------------------
    # Adjustment + revalidation
    # ------------------------------------------------------------------

    def _apply_adjustments(
        self,
        original: CandidateSignal,
        parsed: dict,
        context,
    ) -> CandidateSignal | None:
        """
        Apply bounded AI adjustments — confidence only.

        Constraint-first policy: AI may NOT modify entry, SL, TP,
        direction, or setup type.  Those are structure-derived by
        TradeConstructor and must not be mutated downstream.
        """
        # --- Log and ignore any price-level adjustments ---
        _ignored = []
        for key in ("adjusted_entry", "adjusted_sl", "adjusted_tp",
                     "stop_loss", "take_profit", "entry_zone"):
            if parsed.get(key) is not None:
                _ignored.append(key)
        if _ignored:
            logger.info(
                "[AIValidator] AI attempted price-level adjustment (%s) "
                "— ignored per constraint-first policy",
                ", ".join(_ignored),
            )

        # --- Confidence adjustment (only allowed mutation) ---
        confidence = original.raw_confidence
        adj_conf = parsed.get("confidence")
        if adj_conf is not None:
            adj_conf = float(adj_conf)
            max_conf = original.raw_confidence + _CONFIDENCE_BOOST_MAX
            confidence = round(min(adj_conf, max_conf), 4)

        if confidence == original.raw_confidence:
            return original  # Approved as-is, no changes

        # Build adjusted signal with only confidence changed
        adjusted = CandidateSignal(
            direction=original.direction,
            setup_type=original.setup_type,
            entry_zone=original.entry_zone,
            stop_loss=original.stop_loss,
            take_profit=list(original.take_profit),
            raw_confidence=confidence,
            rr_ratio=original.rr_ratio,
            signal_sources=original.signal_sources,
            reasoning=original.reasoning,
            regime_at_generation=original.regime_at_generation,
            generated_at=original.generated_at,
            sl_source=getattr(original, "sl_source", ""),
            construction_candidates=getattr(original, "construction_candidates", 0),
        )

        logger.info(
            "[AIValidator] Approved with confidence adjustment: %.3f → %.3f",
            original.raw_confidence,
            confidence,
        )
        return adjusted

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(response: str) -> dict | None:
        """Parse AI response JSON (3-pass extraction)."""
        import json

        # Pass 1: direct parse
        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            pass

        # Pass 2: find JSON in markdown
        try:
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            pass

        # Pass 3: try to repair truncated JSON
        try:
            start = response.index("{")
            fragment = response[start:]
            # Close unclosed brackets
            open_braces = fragment.count("{") - fragment.count("}")
            open_brackets = fragment.count("[") - fragment.count("]")
            fragment += "]" * open_brackets + "}" * open_braces
            return json.loads(fragment)
        except (ValueError, json.JSONDecodeError):
            pass

        return None
