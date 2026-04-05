"""
ai/model_hub.py
Centralized AI Model Hub for AlphaLoop v3.

Provides ModelConfig (Pydantic v2), a built-in model catalog covering all
supported providers, and role-based model resolution.

Roles:
    signal        — live cycle: AI review for algo_ai or direct signal generation in ai_signal
    validator     — live cycle: gate before order execution (approve/reject JSON)
    research      — background async: deep degradation analysis over full trade history
    param_suggest — background async: step-by-step parameter change reasoning
    regime        — background hourly: classify market regime (trending/ranging/high_vol)
    fallback      — any role: used when primary provider is unavailable
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from alphaloop.core.types import AIProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

ROLES: tuple[str, ...] = (
    "signal",        # live cycle: AI review for algo_ai or direct signal generation in ai_signal
    "validator",     # live cycle: approve/reject signal before execution
    "research",      # background: deep performance degradation analysis
    "param_suggest", # background: step-by-step parameter change reasoning
    "regime",        # background (hourly): classify current market regime
    "fallback",      # any role: used when primary provider is unavailable
)

DEFAULT_ROLES: dict[str, Optional[str]] = {
    # Default live signal model for hybrid review and pure ai_signal generation.
    # 300 tokens of numerical data → gemini-flash-lite is free and fast enough
    "signal":        "gemini-2.5-flash-lite",

    # Gate before execution: must reliably follow strict rules and output JSON
    # haiku is 5× cheaper than sonnet, still conservative, sufficient for structured validation
    "validator":     "claude-haiku-4-5-20251001",

    # Background async analysis: receives full trade history JSON, identifies degradation root cause
    # gemini-2.5-pro has 1M context and excels at quantitative data analysis; no latency pressure
    "research":      "gemini-2.5-pro",

    # Background: "if sl_atr_mult raised 2.0→2.3, expected impact = X" — step-by-step math reasoning
    # deepseek-reasoner is a cheap reasoning model built exactly for this kind of chained inference
    "param_suggest": "deepseek-reasoner",

    # Hourly regime snapshot: macro context + DXY + structure → trending/ranging/high_vol label
    # flash-lite is free, runs rarely, prompt is short
    "regime":        "gemini-2.5-flash-lite",

    # Provider-down fallback: grok-3-mini has 131K context, solid structured output, cheap
    "fallback":      "grok-3-mini",
}

# Provider -> env-var name holding the API key
PROVIDER_KEY_ENV: dict[AIProvider, str] = {
    AIProvider.GEMINI: "GEMINI_API_KEY",
    AIProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    AIProvider.OPENAI: "OPENAI_API_KEY",
    AIProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
    AIProvider.XAI: "XAI_API_KEY",
    AIProvider.QWEN: "QWEN_API_KEY",
    AIProvider.OLLAMA: "OLLAMA_API_KEY",  # optional — not required for local instances
}

# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """Immutable descriptor for a single AI model."""

    id: str = Field(..., description="Model identifier sent to the provider API")
    provider: AIProvider
    display_name: str = ""
    context_window: int = 128_000
    max_output: int = 8_192
    roles: list[str] = Field(default_factory=list, description="Applicable roles")
    cost_tier: int = Field(
        default=1,
        ge=0,
        le=5,
        description="0=free, 1=cheap, 2=moderate, 3=expensive, 4=premium, 5=ultra",
    )
    endpoint: str = ""
    enabled: bool = True
    latency_ms: int = 1000


# ---------------------------------------------------------------------------
# Built-in model catalog
# ---------------------------------------------------------------------------

BUILTIN_MODELS: list[ModelConfig] = [
    # ── Google Gemini ─────────────────────────────────────────────────────
    ModelConfig(
        id="gemini-2.0-flash",
        provider=AIProvider.GEMINI,
        display_name="Gemini 2.0 Flash",
        context_window=1_048_576,
        max_output=8_192,
        roles=["signal", "validator", "research"],
        cost_tier=1,
        latency_ms=600,
    ),
    ModelConfig(
        id="gemini-2.5-flash",
        provider=AIProvider.GEMINI,
        display_name="Gemini 2.5 Flash",
        context_window=1_048_576,
        max_output=8_192,
        roles=["signal", "validator", "research"],
        cost_tier=1,
        latency_ms=800,
    ),
    ModelConfig(
        id="gemini-2.5-flash-lite",
        provider=AIProvider.GEMINI,
        display_name="Gemini 2.5 Flash Lite",
        context_window=1_048_576,
        max_output=8_192,
        roles=["signal", "regime"],
        cost_tier=0,
        latency_ms=400,
    ),
    ModelConfig(
        id="gemini-2.5-pro",
        provider=AIProvider.GEMINI,
        display_name="Gemini 2.5 Pro",
        context_window=1_048_576,
        max_output=8_192,
        roles=["signal", "validator", "research", "param_suggest"],
        cost_tier=2,
        latency_ms=2000,
    ),
    # ── Anthropic Claude ─────────────────────────────────────────────────
    ModelConfig(
        id="claude-sonnet-4-6",
        provider=AIProvider.ANTHROPIC,
        display_name="Claude Sonnet 4.6",
        context_window=200_000,
        max_output=8_192,
        roles=["signal", "validator", "research"],
        cost_tier=3,
        latency_ms=1200,
    ),
    ModelConfig(
        id="claude-haiku-4-5-20251001",
        provider=AIProvider.ANTHROPIC,
        display_name="Claude Haiku 4.5",
        context_window=200_000,
        max_output=8_192,
        roles=["signal", "validator"],
        cost_tier=1,
        latency_ms=400,
    ),
    ModelConfig(
        id="claude-opus-4-6",
        provider=AIProvider.ANTHROPIC,
        display_name="Claude Opus 4.6",
        context_window=200_000,
        max_output=8_192,
        roles=["research"],
        cost_tier=5,
        latency_ms=3000,
    ),
    # ── OpenAI ────────────────────────────────────────────────────────────
    ModelConfig(
        id="gpt-4o",
        provider=AIProvider.OPENAI,
        display_name="GPT-4o",
        context_window=128_000,
        max_output=16_384,
        roles=["signal", "validator", "research"],
        cost_tier=3,
        endpoint="https://api.openai.com/v1",
        latency_ms=1500,
    ),
    ModelConfig(
        id="gpt-4o-mini",
        provider=AIProvider.OPENAI,
        display_name="GPT-4o Mini",
        context_window=128_000,
        max_output=16_384,
        roles=["signal", "validator"],
        cost_tier=1,
        endpoint="https://api.openai.com/v1",
        latency_ms=600,
    ),
    ModelConfig(
        id="gpt-4.1",
        provider=AIProvider.OPENAI,
        display_name="GPT-4.1",
        context_window=1_048_576,
        max_output=32_768,
        roles=["signal", "validator", "research"],
        cost_tier=2,
        endpoint="https://api.openai.com/v1",
        latency_ms=1200,
    ),
    ModelConfig(
        id="o3-mini",
        provider=AIProvider.OPENAI,
        display_name="o3-mini",
        context_window=200_000,
        max_output=100_000,
        roles=["research"],
        cost_tier=2,
        endpoint="https://api.openai.com/v1",
        latency_ms=4000,
    ),
    # ── DeepSeek ──────────────────────────────────────────────────────────
    ModelConfig(
        id="deepseek-chat",
        provider=AIProvider.DEEPSEEK,
        display_name="DeepSeek V3",
        context_window=64_000,
        max_output=8_192,
        roles=["signal", "validator", "research"],
        cost_tier=1,
        endpoint="https://api.deepseek.com/v1",
        latency_ms=1000,
    ),
    ModelConfig(
        id="deepseek-reasoner",
        provider=AIProvider.DEEPSEEK,
        display_name="DeepSeek R1",
        context_window=64_000,
        max_output=8_192,
        roles=["research", "param_suggest"],
        cost_tier=1,
        endpoint="https://api.deepseek.com/v1",
        latency_ms=3000,
    ),
    # ── xAI / Grok ────────────────────────────────────────────────────────
    ModelConfig(
        id="grok-3",
        provider=AIProvider.XAI,
        display_name="Grok 3",
        context_window=131_072,
        max_output=8_192,
        roles=["signal", "validator", "research"],
        cost_tier=3,
        endpoint="https://api.x.ai/v1",
        latency_ms=1500,
    ),
    ModelConfig(
        id="grok-3-mini",
        provider=AIProvider.XAI,
        display_name="Grok 3 Mini",
        context_window=131_072,
        max_output=8_192,
        roles=["signal", "validator"],
        cost_tier=1,
        endpoint="https://api.x.ai/v1",
        latency_ms=600,
    ),
    ModelConfig(
        id="grok-2",
        provider=AIProvider.XAI,
        display_name="Grok 2",
        context_window=131_072,
        max_output=8_192,
        roles=["signal", "validator"],
        cost_tier=2,
        endpoint="https://api.x.ai/v1",
        latency_ms=1200,
    ),
    # ── Qwen (via Together.ai) ────────────────────────────────────────────
    ModelConfig(
        id="Qwen/Qwen2.5-7B-Instruct-Turbo",
        provider=AIProvider.QWEN,
        display_name="Qwen 2.5 7B (API)",
        context_window=32_768,
        max_output=8_192,
        roles=["signal"],
        cost_tier=1,
        endpoint="https://api.together.ai/v1",
        latency_ms=700,
    ),
    ModelConfig(
        id="Qwen/Qwen2.5-14B-Instruct",
        provider=AIProvider.QWEN,
        display_name="Qwen 2.5 14B (API)",
        context_window=32_768,
        max_output=8_192,
        roles=["signal", "validator"],
        cost_tier=1,
        endpoint="https://api.together.ai/v1",
        latency_ms=1200,
    ),
    ModelConfig(
        id="Qwen/Qwen2.5-32B-Instruct",
        provider=AIProvider.QWEN,
        display_name="Qwen 2.5 32B (API)",
        context_window=32_768,
        max_output=8_192,
        roles=["signal", "validator", "research"],
        cost_tier=2,
        endpoint="https://api.together.ai/v1",
        latency_ms=1500,
    ),
    # ── Local (Ollama) ────────────────────────────────────────────────────
    ModelConfig(
        id="qwen2.5:7b",
        provider=AIProvider.OLLAMA,
        display_name="Qwen 2.5 7B (Local)",
        context_window=32_768,
        max_output=8_192,
        roles=["signal"],
        cost_tier=0,
        endpoint="http://localhost:11434/v1",
        enabled=True,
        latency_ms=500,
    ),
    ModelConfig(
        id="qwen2.5:32b",
        provider=AIProvider.OLLAMA,
        display_name="Qwen 2.5 32B (Local)",
        context_window=32_768,
        max_output=8_192,
        roles=["signal", "validator"],
        cost_tier=0,
        endpoint="http://localhost:11434/v1",
        enabled=True,
        latency_ms=1200,
    ),
]

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# model_id -> ModelConfig for fast lookup
_MODEL_INDEX: dict[str, ModelConfig] = {m.id: m for m in BUILTIN_MODELS}


def get_model_by_id(model_id: str) -> Optional[ModelConfig]:
    """Return ModelConfig by exact model id, or None."""
    return _MODEL_INDEX.get(model_id)


def get_models_for_role(role: str) -> list[ModelConfig]:
    """Return all models tagged with the given role."""
    return [m for m in BUILTIN_MODELS if role in m.roles and m.enabled]


def get_models_by_provider(provider: AIProvider) -> list[ModelConfig]:
    """Return all models from a specific provider."""
    return [m for m in BUILTIN_MODELS if m.provider == provider]


def resolve_role(role: str) -> Optional[ModelConfig]:
    """
    Resolve a role name to its default ModelConfig.

    Looks up the default model_id for the role, then returns its config.
    Returns None if the role has no default or the model is not found.
    """
    model_id = DEFAULT_ROLES.get(role)
    if model_id is None:
        return None
    return get_model_by_id(model_id)


def register_model(config: ModelConfig) -> None:
    """Register a custom model (e.g. user-added Ollama model) at runtime."""
    _MODEL_INDEX[config.id] = config
    if config not in BUILTIN_MODELS:
        BUILTIN_MODELS.append(config)
    logger.info("[ModelHub] Registered model %s (%s)", config.id, config.provider)


def list_all_models() -> list[ModelConfig]:
    """Return all registered models."""
    return list(BUILTIN_MODELS)
