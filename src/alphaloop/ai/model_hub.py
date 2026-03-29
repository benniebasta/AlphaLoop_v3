"""
ai/model_hub.py
Centralized AI Model Hub for AlphaLoop v3.

Provides ModelConfig (Pydantic v2), a built-in model catalog covering all
supported providers, and role-based model resolution.

Roles:
    signal    — generates trade signals
    validator — validates signals before execution
    research  — runs research + parameter evolution
    fallback  — optional fallback if primary model fails
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

ROLES: tuple[str, ...] = ("signal", "validator", "research", "fallback")

DEFAULT_ROLES: dict[str, Optional[str]] = {
    "signal": "gemini-2.5-flash",
    "validator": "claude-sonnet-4-6",
    "research": "claude-sonnet-4-6",
    "fallback": None,
}

# Provider -> env-var name holding the API key
PROVIDER_KEY_ENV: dict[AIProvider, str] = {
    AIProvider.GEMINI: "GEMINI_API_KEY",
    AIProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    AIProvider.OPENAI: "OPENAI_API_KEY",
    AIProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
    AIProvider.XAI: "XAI_API_KEY",
    AIProvider.QWEN: "QWEN_API_KEY",
    AIProvider.OLLAMA: "",
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
        roles=["signal"],
        cost_tier=0,
        latency_ms=400,
    ),
    ModelConfig(
        id="gemini-2.5-pro",
        provider=AIProvider.GEMINI,
        display_name="Gemini 2.5 Pro",
        context_window=1_048_576,
        max_output=8_192,
        roles=["signal", "validator", "research"],
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
        roles=["research"],
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
        enabled=False,
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
        enabled=False,
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
