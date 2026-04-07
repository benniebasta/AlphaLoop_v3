"""
Async multi-asset signal engine.
Generates trade signals using configured AI models.
"""

import json
import logging
import re
import time
from typing import Any

from alphaloop.ai.json_repair import repair_json
from alphaloop.config.assets import get_asset_config, AssetConfig
from alphaloop.core.setup_types import (
    normalize_pipeline_setup_type,
    normalize_schema_setup_type,
)
from alphaloop.pipeline.types import DirectionHypothesis
from alphaloop.signals.schema import TradeSignal

logger = logging.getLogger(__name__)


async def _invoke_ai_caller(
    ai_caller: Any,
    model_id: str,
    messages: list[dict[str, str]],
    **kwargs: Any,
) -> str:
    """Support either an AICaller instance or a raw async callback."""
    call_model = getattr(ai_caller, "call_model", None)
    if callable(call_model):
        return await call_model(model_id, messages, **kwargs)
    if callable(ai_caller):
        return await ai_caller(model_id, messages, **kwargs)
    raise TypeError("AI caller must be callable or expose call_model()")


def _field(source: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a dict-like or attribute-based object."""
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _build_signal_system_prompt(asset: AssetConfig) -> str:
    """Build the signal generation system prompt."""
    return f"""You are a professional {asset.display_name} ({asset.symbol}) trader with 15 years of experience specialising in {asset.asset_class} markets.

Best trading sessions: {', '.join(asset.best_sessions)}

{asset.ai_context}

Your task: analyse the provided market data and generate ONE high-probability trade signal.

CRITICAL RULES:
1. Prefer pullback setups over breakout chasing
2. Only signal during appropriate sessions for this asset
3. Never signal when RSI is extreme (>{asset.rsi_extreme_ob} overbought or <{asset.rsi_extreme_os} oversold)
4. Entry zone must be narrow (< 0.5 ATR wide)
5. SL placement by direction:
   - BUY signal:  SL must be BELOW entry_zone[0] (below the low of the entry zone)
   - SELL signal: SL must be ABOVE entry_zone[1] (above the high of the entry zone)
   TP placement by direction:
   - BUY signal:  TP must be ABOVE entry_zone[1]
   - SELL signal: TP must be BELOW entry_zone[0]
6. If no clear setup exists, return {{"trend": "neutral", "confidence": 0.0, "reasoning": "No setup"}}
7. SL distance: minimum {asset.sl_min_points} points | maximum {asset.sl_max_points} points
8. SL size: {asset.sl_atr_mult}x ATR | TP1 minimum R:R: {asset.tp1_rr} | TP2: {asset.tp2_rr}
9. Minimum confidence to signal: {asset.min_confidence}
10. MODE: SWING — favour H1 structure, SL 1-2x ATR, TP1 at {asset.tp1_rr}R minimum

REGIME-BASED RULES (adapt your analysis based on REGIME in Tier 1):
- TRENDING:  favour continuation and pullback setups; TP up to 2.5x ATR; trade with H1 trend only; breakouts allowed on confirmed structure breaks
- RANGING:   favour range_bounce setups only; TP at range midpoint or opposite edge; REJECT breakout and continuation setups; SL must be outside the range
- VOLATILE:  require 4+ confluences on dominant side; reduce confidence by 0.10 from your estimate; tighten entry zone to < 0.3 ATR; SL must clear recent spike
- DEAD:      output confidence 0.0 — do not generate a trade signal
- NEUTRAL:   apply standard rules above

CONFLUENCE GUIDANCE (use the CONFLUENCE scores in Tier 2):
- confluence < 3 → confidence must be below 0.65
- confluence 3–4 → confidence range 0.65–0.75
- confluence >= 5 → confidence may reach 0.85+

ANALYSIS ORDER: Analyse Tier 1–2 first. Tiers 3–5 are supporting data only.

You MUST respond with ONLY valid JSON:
{{
  "trend": "bullish" or "bearish" or "neutral",
  "setup": "pullback" or "breakout" or "reversal" or "continuation" or "range_bounce",
  "entry_zone": [lower_price, upper_price],
  "stop_loss": price,
  "take_profit": [tp1, tp2],
  "confidence": 0.0-1.0,
  "reasoning": "Concise explanation citing regime, confluence count, and key levels (max 200 chars)"
}}

No markdown, no preamble. Only the raw JSON object."""


def build_signal_system_prompt(asset: AssetConfig, prompt_instructions: str = "") -> str:
    """Build the signal generation system prompt with optional strategy instructions."""
    base = _build_signal_system_prompt(asset)
    extra = (prompt_instructions or "").strip()
    if not extra:
        return base
    return f"{base}\n\nStrategy instructions:\n{extra}"


def _build_hypothesis_system_prompt(asset: AssetConfig) -> str:
    """Build a direction-only system prompt (no SL/TP instructions).

    Used by :meth:`MultiAssetSignalEngine.generate_hypothesis` in the
    constraint-first pipeline where SL/TP are derived from market
    structure downstream by the TradeConstructor.
    """
    return f"""You are a professional {asset.display_name} ({asset.symbol}) trader with 15 years of experience specialising in {asset.asset_class} markets.

Best trading sessions: {', '.join(asset.best_sessions)}

{asset.ai_context}

Your task: analyse the provided market data and output a directional view.

CRITICAL RULES:
1. Prefer pullback setups over breakout chasing
2. Only signal during appropriate sessions for this asset
3. Never signal when RSI is extreme (>{asset.rsi_extreme_ob} overbought or <{asset.rsi_extreme_os} oversold)
4. If no clear setup exists, return neutral and always explain WHY in the reasoning field (e.g. "EMA not crossed, RSI at 51, no directional confluence")
5. Minimum confidence to signal: {asset.min_confidence}

IMPORTANT: Do NOT output stop_loss, take_profit, or entry_zone. Those are derived automatically from market structure. You only output direction and confidence.

REGIME-BASED RULES (adapt your analysis based on REGIME in Tier 1):
- TRENDING:  favour continuation and pullback setups; trade with H1 trend only
- RANGING:   favour range_bounce setups only; REJECT breakout and continuation
- VOLATILE:  require 4+ confluences on dominant side; reduce confidence by 0.10
- DEAD:      output confidence 0.0 — do not generate a trade signal
- NEUTRAL:   apply standard rules above

CONFLUENCE GUIDANCE (use the CONFLUENCE scores in Tier 2):
- confluence < 3 → confidence must be below 0.65
- confluence 3–4 → confidence range 0.65–0.75
- confluence >= 5 → confidence may reach 0.85+

ANALYSIS ORDER: Analyse Tier 1–2 first. Tiers 3–5 are supporting data only.

You MUST respond with ONLY valid JSON:
{{
  "trend": "bullish" or "bearish" or "neutral",
  "setup": "pullback" or "breakout" or "reversal" or "continuation" or "range_bounce",
  "confidence": 0.0-1.0,
  "reasoning": "Concise explanation citing regime, confluence count, and key levels (max 200 chars)"
}}

No markdown, no preamble. Only the raw JSON object."""


def _compute_confluence(context: dict) -> tuple[int, int, list[str], list[str]]:
    """
    Compute bullish and bearish confluence scores from available context data.

    Checks 6 factors for each direction without requiring a pre-determined direction.
    Returns: (bull_score, bear_score, bull_factors, bear_factors)
    """
    h1 = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
    m15 = context.get("timeframes", {}).get("M15", {}).get("indicators", {})

    # Support both AttrDict (loop context) and dict
    price_raw = context.get("current_price") or context.get("price") or {}
    bid = float(_field(price_raw, "bid") or 0)

    session_raw = context.get("session") or {}
    sess_score = float(_field(session_raw, "score") or 0)

    bull: list[str] = []
    bear: list[str] = []

    # 1. H1 trend bias (price vs EMA200)
    h1_trend = h1.get("trend_bias")
    h1_ema200 = h1.get("ema200")
    if not h1_trend and h1_ema200 and bid > 0:
        h1_trend = "bullish" if bid > h1_ema200 else "bearish"
    if h1_trend == "bullish":
        bull.append("H1 trend bullish")
    elif h1_trend == "bearish":
        bear.append("H1 trend bearish")

    # 2. M15 trend bias
    m15_trend = m15.get("trend_bias")
    m15_ema200 = m15.get("ema200")
    if not m15_trend and m15_ema200 and bid > 0:
        m15_trend = "bullish" if bid > m15_ema200 else "bearish"
    if m15_trend == "bullish":
        bull.append("M15 trend bullish")
    elif m15_trend == "bearish":
        bear.append("M15 trend bearish")

    # 3. Above/below H1 EMA200
    if h1_ema200 and bid > 0:
        if bid > h1_ema200:
            bull.append("Above H1 EMA200")
        else:
            bear.append("Below H1 EMA200")

    # 4. M15 BOS direction
    bos = m15.get("bos") or {}
    if isinstance(bos, dict):
        if bos.get("bullish_bos"):
            bull.append("M15 BOS bullish break")
        if bos.get("bearish_bos"):
            bear.append("M15 BOS bearish break")

    # 5. FVG presence
    fvg = m15.get("fvg") or {}
    if isinstance(fvg, dict):
        if fvg.get("bullish"):
            bull.append("Bullish FVG present")
        if fvg.get("bearish"):
            bear.append("Bearish FVG present")

    # 6. Premium session (benefits both directions)
    if sess_score >= 0.8:
        bull.append("Premium session")
        bear.append("Premium session")

    return len(bull), len(bear), bull, bear


def _fmt_fvg(fvg_data: dict | None) -> str:
    """Format FVG data for the AI prompt."""
    if not fvg_data:
        return "none"
    bull = fvg_data.get("bullish", [])
    bear = fvg_data.get("bearish", [])
    parts = []
    if bull:
        levels = ", ".join(f"{g['bottom']}-{g['top']}" for g in bull[-2:])
        parts.append(f"bullish [{levels}]")
    if bear:
        levels = ", ".join(f"{g['bottom']}-{g['top']}" for g in bear[-2:])
        parts.append(f"bearish [{levels}]")
    return " | ".join(parts) if parts else "none"


def _fmt_bos(bos_data: dict | None) -> str:
    """Format BOS data for the AI prompt."""
    if not bos_data:
        return "none"
    parts = []
    if bos_data.get("bullish_bos"):
        parts.append(f"BULLISH break (+{bos_data.get('bullish_break_atr', 0):.2f}ATR above {bos_data.get('swing_high')})")
    if bos_data.get("bearish_bos"):
        parts.append(f"BEARISH break ({bos_data.get('bearish_break_atr', 0):.2f}ATR below {bos_data.get('swing_low')})")
    if not parts:
        return f"none | swing_high={bos_data.get('swing_high')} swing_low={bos_data.get('swing_low')}"
    return " | ".join(parts)


def _build_signal_user_prompt(
    asset: AssetConfig,
    context: dict,
    tool_results: list[dict] | None = None,
) -> str:
    """Build the tiered user prompt with market context and optional tool readings."""
    h1 = context.get("timeframes", {}).get("H1", {}).get("indicators", {})
    m15 = context.get("timeframes", {}).get("M15", {}).get("indicators", {})
    session = context.get("session", {})
    price = context.get("current_price", {})
    dxy = context.get("dxy", {})
    sentiment = context.get("macro_sentiment", context.get("sentiment", {}))

    # Regime: pre-computed if available, else derive inline
    regime = context.get("regime") or "neutral"
    macro_regime = context.get("macro_regime") or "neutral"
    if regime == "neutral":
        # Try to compute inline if not pre-set (live loop context)
        try:
            from alphaloop.data.market_context import _classify_regime, _classify_macro_regime
            _regime_ind = {
                "choppiness": m15.get("choppiness"),
                "atr_pct": h1.get("atr_pct") or m15.get("atr_pct"),
                "adx": m15.get("adx"),
            }
            regime = _classify_regime(_regime_ind)
            macro_regime = _classify_macro_regime(dxy, sentiment)
        except Exception:
            pass

    # Confluence scores
    bull_n, bear_n, bull_factors, bear_factors = _compute_confluence(context)

    # H1 fields
    h1_ema200 = h1.get("ema200", h1.get("ema_trend"))
    h1_trend = h1.get("trend_bias", "N/A")

    # Compute h1 trend if missing
    bid_val = _field(price, "bid") or 0
    if h1_trend == "N/A" and h1_ema200 and bid_val:
        h1_trend = "bullish" if float(bid_val) > float(h1_ema200) else "bearish"

    # M15 structure fields
    bos = m15.get("bos")
    fvg = m15.get("fvg")
    swing_struct = m15.get("swing_structure", "N/A")
    adx_val = m15.get("adx", "N/A")
    macd_hist = m15.get("macd_histogram", "N/A")
    bb_pct_b = m15.get("bb_pct_b", "N/A")
    ema200_m15 = m15.get("ema200", "N/A")
    vol_ratio = m15.get("volume_ratio", "N/A")
    m15_trend = m15.get("trend_bias", "N/A")
    chop_raw = m15.get("choppiness")
    chop_val = chop_raw.get("ci", "N/A") if isinstance(chop_raw, dict) else (chop_raw or "N/A")

    # News summary for macro section
    news_raw = context.get("upcoming_news", context.get("news", []))
    if news_raw:
        news_lines = [
            f"{ev.get('impact', '?')} — {ev.get('name', '?')} @ {ev.get('time', '?')}"
            for ev in news_raw[:3]
        ]
        news_summary = " | ".join(news_lines)
    else:
        news_summary = "none in window"

    # Tool readings section (Tier 5)
    tool_section = ""
    if tool_results:
        lines = []
        for r in tool_results:
            name = r.get("tool_name", "?")
            passed = r.get("passed", True)
            reason = r.get("reason", "")
            bias = r.get("bias", "neutral")
            size_mod = r.get("size_modifier", 1.0)
            status = "PASS" if passed else "WARN"
            line = f"  {name}: {status} bias:{bias}"
            if size_mod < 1.0:
                line += f" size:{size_mod:.2f}"
            if reason:
                line += f" — {reason}"
            lines.append(line)
        tool_section = "\n".join(lines)

    bull_factor_str = " | ".join(bull_factors) if bull_factors else "none"
    bear_factor_str = " | ".join(bear_factors) if bear_factors else "none"

    return f"""=== {asset.display_name.upper()} ({asset.symbol}) SIGNAL ANALYSIS REQUEST ===

══ TIER 1 — MARKET STATE (analyse first) ══
REGIME: {regime.upper()}
MACRO REGIME: {macro_regime.upper()}
SESSION: {_field(session, 'name')} (quality {_field(session, 'score')})
CURRENT PRICE: Bid={_field(price, 'bid')} Ask={_field(price, 'ask')} Spread={_field(price, 'spread')} pts

══ TIER 2 — PRIMARY STRUCTURE (entry logic) ══
M15 BOS: {_fmt_bos(bos)}
M15 FVG: {_fmt_fvg(fvg)}
M15 SWING: {swing_struct}
BULLISH CONFLUENCE: {bull_n}/6  [{bull_factor_str}]
BEARISH CONFLUENCE: {bear_n}/6  [{bear_factor_str}]

══ TIER 3 — TREND CONTEXT (direction filter) ══
H1:  EMA{m15.get('ema_fast_period', 21)}={h1.get('ema_fast')} EMA{m15.get('ema_slow_period', 55)}={h1.get('ema_slow')} EMA200={h1_ema200} | RSI={h1.get('rsi', 'N/A')} | ATR={h1.get('atr')} ({h1.get('atr_pct', 'N/A')}%) | Trend={h1_trend}
M15: EMA{m15.get('ema_fast_period', 21)}={m15.get('ema_fast')} EMA{m15.get('ema_slow_period', 55)}={m15.get('ema_slow')} EMA200={ema200_m15} | RSI={m15.get('rsi')} | ATR={m15.get('atr')} | ADX={adx_val} | MACD_Hist={macd_hist}
M15: BB_%B={bb_pct_b} | Choppiness={chop_val} | Volume_Ratio={vol_ratio} | Trend={m15_trend}

ASSET PARAMETERS:
  SL: {asset.sl_atr_mult}x ATR | TP1: {asset.tp1_rr}R | TP2: {asset.tp2_rr}R | SL range: {asset.sl_min_points}–{asset.sl_max_points} pts

══ TIER 4 — MACRO CONTEXT (background) ══
DXY: {_field(dxy, 'bias', 'N/A')} | strength={_field(dxy, 'strength_label', _field(dxy, 'strength', 'N/A'))} | trend={_field(dxy, 'trend', 'N/A')} | level={_field(dxy, 'level', _field(dxy, 'current_level', 'N/A'))}
SENTIMENT: {_field(sentiment, 'bias', 'N/A')}
UPCOMING NEWS: {news_summary}

══ TIER 5 — TOOL READINGS (confirmation) ══
{tool_section if tool_section else "  (no tool readings)"}

Analyse Tier 1–2 first. Generate a {asset.display_name} trade signal now."""


class MultiAssetSignalEngine:
    """
    Generates trade signals using AI models.
    Async — uses an AI caller callback.
    """

    _TRUNCATION_ALERT_THRESHOLD = 3
    _CIRCUIT_BREAKER_THRESHOLD = 5     # consecutive failures before circuit trips
    _CIRCUIT_BREAKER_PAUSE_SEC = 600   # 10-minute pause before retrying

    def __init__(self, symbol: str = "XAUUSD", *, event_bus=None):
        self.asset = get_asset_config(symbol)
        self.symbol = self.asset.symbol
        self.last_error: str | None = None
        self.last_neutral_reason: str | None = None
        self._consecutive_truncations: int = 0
        self._circuit_breaker_until: float = 0.0  # monotonic timestamp; 0 = not tripped
        self._event_bus = event_bus
        self._pending_circuit_alert: bool = False  # set by _record_truncation; published async in generate_signal

    async def generate_signal(
        self,
        context: dict,
        *,
        ai_caller=None,
        model_id: str = "",
        strategy_params: dict | None = None,
        tool_results: list[dict] | None = None,
        prompt_instructions: str = "",
    ) -> TradeSignal | None:
        """
        Generate a trade signal from market context.
        ai_caller: async callable(model_id, messages, **kwargs) -> str
        strategy_params: optional strategy-specific params for prompt building
        Returns None if no signal or neutral.
        """
        if ai_caller is None:
            logger.warning("[signal-engine] No AI caller provided")
            self.last_error = "No AI caller configured — check model settings"
            return None

        # Circuit breaker — pause AI calls after repeated failures
        now = time.monotonic()
        if now < self._circuit_breaker_until:
            remaining = int(self._circuit_breaker_until - now)
            logger.warning(
                "[signal-engine] %s — AI circuit breaker active, %ds remaining. "
                "Skipping AI call.",
                self.symbol, remaining,
            )
            self.last_error = f"AI circuit breaker active ({remaining}s remaining)"
            return None

        self.last_error = None

        system_prompt = build_signal_system_prompt(self.asset, prompt_instructions)
        user_prompt = _build_signal_user_prompt(self.asset, context, tool_results=tool_results)

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            # Auto-inject fallback model so quota exhaustion doesn't hard-fail
            extra: dict[str, Any] = {"max_tokens": 800}
            try:
                from alphaloop.ai.model_hub import resolve_role
                fb_cfg = resolve_role("fallback")
                if fb_cfg and fb_cfg.id != model_id:
                    extra["fallback_models"] = [fb_cfg.id]
            except Exception:
                pass
            raw = await _invoke_ai_caller(
                ai_caller,
                model_id,
                messages,
                **extra,
            )
        except Exception as e:
            logger.error("[signal-engine] AI call failed: %s", e)
            self.last_error = f"AI call failed: {e}"
            return None

        if not raw:
            self.last_error = "AI returned empty response"
            return None

        result = self._parse_signal(raw)

        # C-04: publish AlertTriggered if circuit just tripped during parse
        if self._pending_circuit_alert and self._event_bus is not None:
            self._pending_circuit_alert = False
            from alphaloop.core.events import AlertTriggered
            await self._event_bus.publish(AlertTriggered(
                severity="HIGH",
                rule_name="ai_circuit_breaker",
                message=(
                    f"AI signal engine circuit breaker tripped for {self.symbol}: "
                    f"{self._consecutive_truncations} consecutive failures. "
                    f"AI calls paused for {self._CIRCUIT_BREAKER_PAUSE_SEC}s. "
                    "Check provider quota, max_tokens, and connectivity."
                ),
                symbol=self.symbol,
            ))

        return result

    def _record_truncation(self) -> None:
        """Increment consecutive truncation/failure counter and trip circuit breaker if threshold reached."""
        self._consecutive_truncations += 1
        if self._consecutive_truncations >= self._TRUNCATION_ALERT_THRESHOLD:
            logger.warning(
                "[signal-engine] %s — %d consecutive JSON failures.",
                self.symbol, self._consecutive_truncations,
            )
        if self._consecutive_truncations >= self._CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_breaker_until = time.monotonic() + self._CIRCUIT_BREAKER_PAUSE_SEC
            self._pending_circuit_alert = True
            logger.critical(
                "[signal-engine][CIRCUIT-BREAKER] %s — %d consecutive AI failures. "
                "AI calls paused for %ds. Check provider quota, max_tokens, and connectivity.",
                self.symbol, self._consecutive_truncations, self._CIRCUIT_BREAKER_PAUSE_SEC,
            )

    @staticmethod
    def _repair_json(s: str) -> str:
        """Close unclosed arrays/objects in truncated JSON. Delegates to ai.json_repair."""
        return repair_json(s)

    def _parse_signal(self, raw_text: str) -> TradeSignal | None:
        """Parse AI response into a TradeSignal — 3-pass with JSON repair."""
        data = None

        # Pass 1 — direct parse
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            pass

        if data is None:
            clean = re.sub(r"```(?:json)?\s*", "", raw_text).strip()
            start = clean.find("{")
            if start == -1:
                logger.warning("[signal-engine] No JSON object in response: %s", raw_text[:200])
                self.last_error = "AI returned no JSON object"
                self._record_truncation()
                return None

            # Pass 2 — find first { … last }
            end = clean.rfind("}")
            if end > start:
                try:
                    data = json.loads(clean[start : end + 1])
                except json.JSONDecodeError:
                    pass

            # Pass 3 — auto-repair truncated JSON
            if data is None:
                repaired = self._repair_json(clean[start:])
                try:
                    data = json.loads(repaired)
                    self._record_truncation()
                    logger.info("[signal-engine] Repaired truncated JSON response")
                except json.JSONDecodeError:
                    self._record_truncation()
                    logger.warning(
                        "[signal-engine] Could not parse JSON after repair — skipping. "
                        "Raw (first 300 chars): %s", raw_text[:300],
                    )
                    self.last_error = "AI returned unparseable JSON"
                    return None

        # Fill defaults for fields that may be missing in truncated responses
        data.setdefault("confidence", 0.5)
        data.setdefault("reasoning", "(truncated response — auto-repaired)")
        data["setup"] = normalize_schema_setup_type(data.get("setup"))

        # take_profit is required — can't trade without TP levels
        tp = data.get("take_profit")
        if not tp or (isinstance(tp, list) and not any(v for v in tp if v)):
            self._record_truncation()
            logger.warning(
                "[signal-engine] %s — signal missing take_profit after repair — skipping.",
                self.symbol,
            )
            self.last_error = "AI signal missing take_profit"
            return None

        # Normalize entry_zone order
        raw_ez = data.get("entry_zone")
        if isinstance(raw_ez, list) and len(raw_ez) == 2:
            lo, hi = float(raw_ez[0]), float(raw_ez[1])
            if lo > hi:
                lo, hi = hi, lo
            if lo == hi:
                spread = max(round(lo * 0.0001, 2), 0.01)
                lo, hi = lo - spread, hi + spread
            data["entry_zone"] = [lo, hi]

        # Skip neutral or zero-price placeholders
        if data.get("trend") == "neutral" or data.get("confidence", 0) < 0.1:
            self.last_neutral_reason = (
                data.get("reasoning")
                or f"AI neutral (conf={data.get('confidence', 0):.2f}) — no reasoning provided"
            )
            logger.info("[signal-engine] Neutral signal — %s", self.last_neutral_reason)
            self.last_error = None
            return None

        entry = data.get("entry_zone", [0, 0])
        sl = data.get("stop_loss", 0)
        e0 = entry[0] if isinstance(entry, list) and len(entry) > 0 else 0
        e1 = entry[1] if isinstance(entry, list) and len(entry) > 1 else 0
        if not any(v > 0 for v in (e0, e1, sl)):
            logger.info("[signal-engine] %s — zero-price placeholder (trend=%s conf=%.2f)",
                        self.symbol, data.get("trend"), data.get("confidence", 0))
            self.last_neutral_reason = (
                f"AI returned zero prices (entry=[{e0},{e1}] SL={sl}) — model may be confused about price scale"
            )
            return None

        try:
            signal = TradeSignal(**data)
            self._consecutive_truncations = 0
            self.last_neutral_reason = None
            logger.info(
                "[signal-engine] %s signal: %s conf=%.2f RR=%.2f",
                self.symbol,
                signal.direction,
                signal.confidence,
                signal.rr_ratio_tp1 or 0,
            )
            self.last_error = None
            return signal
        except Exception as e:
            logger.error("[signal-engine] Signal validation failed: %s", e)
            self.last_error = f"AI signal validation failed: {e}"
            return None

    # ------------------------------------------------------------------
    # Constraint-first: direction-only hypothesis (no SL/TP)
    # ------------------------------------------------------------------

    async def generate_hypothesis(
        self,
        context: dict,
        *,
        ai_caller=None,
        model_id: str = "",
        tool_results: list[dict] | None = None,
        prompt_instructions: str = "",
    ) -> DirectionHypothesis | None:
        """Generate a direction hypothesis without SL/TP.

        The AI is asked only for direction, confidence, setup type, and
        reasoning.  Any SL/TP fields in the response are ignored.
        """
        if ai_caller is None:
            logger.warning("[signal-engine] No AI caller provided")
            self.last_error = "No AI caller configured — check model settings"
            return None

        # Circuit breaker — pause AI calls after repeated failures
        now = time.monotonic()
        if now < self._circuit_breaker_until:
            remaining = int(self._circuit_breaker_until - now)
            logger.warning(
                "[signal-engine] %s — AI circuit breaker active, %ds remaining. "
                "Skipping hypothesis call.",
                self.symbol, remaining,
            )
            self.last_error = f"AI circuit breaker active ({remaining}s remaining)"
            return None

        self.last_error = None

        system_prompt = _build_hypothesis_system_prompt(self.asset)
        if prompt_instructions:
            system_prompt += f"\n\nStrategy instructions:\n{prompt_instructions.strip()}"

        # T5 tier: prefer explicitly passed tool_results, fall back to
        # context.tool_results populated by the orchestrator's hypothesis-tool run.
        _tool_results = tool_results or getattr(context, "tool_results", None)
        user_prompt = _build_signal_user_prompt(
            self.asset, context, tool_results=_tool_results,
        )

        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            extra: dict[str, Any] = {"max_tokens": 500}
            try:
                from alphaloop.ai.model_hub import resolve_role
                fb_cfg = resolve_role("fallback")
                if fb_cfg and fb_cfg.id != model_id:
                    extra["fallback_models"] = [fb_cfg.id]
            except Exception:
                pass
            raw = await _invoke_ai_caller(ai_caller, model_id, messages, **extra)
        except Exception as e:
            logger.error("[signal-engine] AI call failed: %s", e)
            self.last_error = f"AI call failed: {e}"
            return None

        if not raw:
            self.last_error = "AI returned empty response"
            return None

        return self._parse_hypothesis(raw)

    def _parse_hypothesis(self, raw_text: str) -> DirectionHypothesis | None:
        """Parse AI response into a DirectionHypothesis.

        Ignores any SL/TP/entry_zone fields the AI may return.
        """
        data = None

        # Parse JSON (same 3-pass approach)
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            pass

        if data is None:
            clean = re.sub(r"```(?:json)?\s*", "", raw_text).strip()
            start = clean.find("{")
            if start == -1:
                self.last_error = "AI returned no JSON object"
                return None

            end = clean.rfind("}")
            if end > start:
                try:
                    data = json.loads(clean[start : end + 1])
                except json.JSONDecodeError:
                    pass

            if data is None:
                repaired = self._repair_json(clean[start:])
                try:
                    data = json.loads(repaired)
                except json.JSONDecodeError:
                    self.last_error = "AI returned unparseable JSON"
                    return None

        # Log and ignore any SL/TP fields the AI may have returned
        _ignored_fields = []
        for key in ("stop_loss", "take_profit", "entry_zone"):
            if key in data:
                _ignored_fields.append(key)
                del data[key]
        if _ignored_fields:
            logger.info(
                "[signal-engine] AI returned SL/TP fields %s — ignored per "
                "constraint-first policy",
                _ignored_fields,
            )

        trend = data.get("trend", "neutral")
        confidence = float(data.get("confidence", 0.0))

        if trend == "neutral" or confidence < 0.1:
            self.last_neutral_reason = (
                data.get("reasoning")
                or f"AI neutral (conf={data.get('confidence', 0):.2f}) — no reasoning provided"
            )
            self.last_error = None
            return None

        direction = "BUY" if trend == "bullish" else "SELL"
        setup_tag = normalize_pipeline_setup_type(data.get("setup", "pullback"))
        reasoning = data.get("reasoning", "(no reasoning)")[:500]

        from datetime import datetime, timezone
        hypothesis = DirectionHypothesis(
            direction=direction,
            confidence=round(min(max(confidence, 0.0), 1.0), 3),
            setup_tag=setup_tag,
            reasoning=reasoning,
            source_names="ai_signal",
            generated_at=datetime.now(timezone.utc),
        )

        self.last_neutral_reason = None
        self.last_error = None
        logger.info(
            "[signal-engine] %s hypothesis: %s conf=%.2f setup=%s",
            self.symbol, direction, hypothesis.confidence, setup_tag,
        )
        return hypothesis
