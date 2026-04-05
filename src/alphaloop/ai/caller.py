"""
ai/caller.py
Universal async AI caller — routes any model_id to the correct provider.

Usage:
    caller = AICaller(api_keys={"anthropic": "sk-...", "gemini": "AI..."})
    text = await caller.call_model("claude-sonnet-4-6", messages=[...])
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from alphaloop.ai.model_hub import (
    PROVIDER_KEY_ENV,
    ModelConfig,
    get_model_by_id,
    resolve_role,
)
from alphaloop.ai.providers.anthropic import AnthropicProvider
from alphaloop.ai.providers.gemini import GeminiProvider
from alphaloop.ai.providers.ollama import OllamaProvider
from alphaloop.ai.providers.openai_compat import OpenAICompatProvider
from alphaloop.ai.rate_limiter import AsyncRateLimiter
from alphaloop.core.constants import AI_RATE_LIMIT_PER_MIN, AI_RATE_LIMIT_WINDOW_SEC
from alphaloop.core.errors import AlphaLoopError, ConfigError, RateLimitError
from alphaloop.core.types import AIProvider

logger = logging.getLogger(__name__)

# Default retry configuration
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY = 1.0  # seconds


class AICaller:
    """
    Universal async AI caller with per-provider rate limiting.

    Parameters
    ----------
    api_keys : dict[str, str] | None
        Provider name -> API key. Falls back to env vars if not supplied.
    rate_limiter : AsyncRateLimiter | None
        Custom rate limiter. A default (5 calls/min/provider, from AI_RATE_LIMIT_PER_MIN) is created
        if not supplied.
    """

    def __init__(
        self,
        api_keys: dict[str, str] | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._api_keys: dict[str, str] = api_keys or {}
        self._rate_limiter = rate_limiter or AsyncRateLimiter(
            max_calls=AI_RATE_LIMIT_PER_MIN, window_seconds=AI_RATE_LIMIT_WINDOW_SEC
        )

    async def __call__(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Allow AICaller instances to be used as async callables."""
        return await self.call_model(model_id, messages, **kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def call_model(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        system: str | None = None,
        timeout: float = 60.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        fallback_models: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Call a model by its hub model_id and return the text response.

        Supports retry with exponential backoff and fallback to alternative models.

        Parameters
        ----------
        model_id : str
            Model identifier from the model hub catalog.
        messages : list of dicts
            Chat messages (role + content).
        max_tokens : int
            Max response tokens.
        temperature : float
            Sampling temperature.
        system : str or None
            Optional system prompt.
        timeout : float
            HTTP timeout in seconds.
        max_retries : int
            Number of retries on the primary model before trying fallbacks.
        retry_delay : float
            Base delay between retries (doubled each attempt).
        fallback_models : list of str or None
            Model IDs to try if the primary model fails after retries.

        Returns
        -------
        str
            The model's text response.

        Raises
        ------
        ConfigError
            If the model is not found in the hub or API key is missing.
        RateLimitError
            If the per-provider rate limit is exceeded.
        AlphaLoopError
            On provider API errors after all retries and fallbacks exhausted.
        """
        # Build the full chain: primary model (with retries) + fallbacks
        models_to_try = [model_id] + (fallback_models or [])
        last_error: Exception | None = None
        any_model_found = False

        for idx, mid in enumerate(models_to_try):
            cfg = get_model_by_id(mid)
            if cfg is None:
                logger.warning("[caller] Model '%s' not in hub — skipping", mid)
                continue
            if not cfg.enabled:
                logger.warning("[caller] Model '%s' disabled — skipping", mid)
                continue
            any_model_found = True

            # Retries only for the primary model; fallbacks get 1 attempt each
            attempts = (max_retries + 1) if idx == 0 else 1
            delay = retry_delay

            for attempt in range(1, attempts + 1):
                _t_start = time.perf_counter()
                _call_success = False
                try:
                    provider = cfg.provider
                    await self._rate_limiter.acquire(provider)
                    api_key = self._resolve_key(provider)

                    is_retry = idx > 0 or attempt > 1
                    label = f"{'fallback ' if idx > 0 else ''}{'retry ' if attempt > 1 else ''}"
                    logger.info(
                        "[caller] %s%s/%s — sending request (attempt %d/%d)",
                        label, provider, mid, attempt, attempts,
                    )

                    dispatch = self._dispatch(
                        cfg=cfg,
                        api_key=api_key,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system,
                        timeout=timeout,
                        **kwargs,
                    )
                    text = await asyncio.wait_for(dispatch, timeout=timeout)
                    if is_retry:
                        logger.info(
                            "[caller] %s%s/%s succeeded on attempt %d",
                            label, provider, mid, attempt,
                        )
                    else:
                        logger.info("[caller] %s/%s — response received", provider, mid)
                    _call_success = True
                    return text

                except RateLimitError:
                    # Don't retry rate limits — propagate immediately
                    raise
                except asyncio.TimeoutError:
                    last_error = AlphaLoopError(
                        f"AI call timed out after {timeout:.1f}s"
                    )
                    logger.warning(
                        "[caller] %s/%s attempt %d/%d timed out after %.1fs",
                        cfg.provider, mid, attempt, attempts, timeout,
                    )
                    if attempt < attempts:
                        await asyncio.sleep(delay)
                        delay *= 2  # exponential backoff
                except ConfigError:
                    # Config errors (missing key) won't fix on retry
                    logger.warning("[caller] Config error for %s — skipping", mid)
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "[caller] %s/%s attempt %d/%d failed: %s",
                        cfg.provider, mid, attempt, attempts, exc,
                    )
                    if attempt < attempts:
                        await asyncio.sleep(delay)
                        delay *= 2  # exponential backoff
                finally:
                    # Record per-call performance metrics (never blocks the caller)
                    try:
                        from alphaloop.ai.performance import model_performance_tracker as _pt
                        _pt.record_call(
                            mid,
                            latency_ms=(time.perf_counter() - _t_start) * 1000,
                            success=_call_success,
                        )
                    except Exception:
                        pass

        # All models exhausted — distinguish "not found" from "disabled"
        if not any_model_found:
            # Check if the primary model exists but is disabled
            primary_cfg = get_model_by_id(model_id)
            if primary_cfg is not None and not primary_cfg.enabled:
                raise ConfigError(f"Model '{model_id}' is disabled")
            raise ConfigError(
                f"Model '{model_id}' not found in model hub"
            )
        raise AlphaLoopError(
            f"AI call failed for {model_id} (and {len(models_to_try) - 1} fallbacks): "
            f"{last_error}"
        ) from last_error

    async def call_role(
        self,
        role: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """
        Call the model assigned to a given role, with automatic fallback.

        Automatically resolves the "fallback" role model and includes it
        in the fallback chain if the primary model fails.

        Parameters
        ----------
        role : str
            One of "signal", "validator", "research", "fallback".
        messages : list of dicts
            Chat messages.
        **kwargs
            Forwarded to call_model.

        Returns
        -------
        str
            The model's text response.
        """
        cfg = resolve_role(role)
        if cfg is None:
            raise ConfigError(f"No model configured for role '{role}'")

        # Auto-include fallback model if not already the fallback role
        if "fallback_models" not in kwargs and role != "fallback":
            fallback_cfg = resolve_role("fallback")
            if fallback_cfg and fallback_cfg.id != cfg.id:
                kwargs["fallback_models"] = [fallback_cfg.id]

        return await self.call_model(cfg.id, messages, **kwargs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_key(self, provider: AIProvider) -> str:
        """Resolve API key: explicit dict -> env var -> 'local' sentinel (Ollama optional)."""
        # Check explicit keys dict
        key = self._api_keys.get(provider, "")
        if key:
            return key

        # Check env var
        env_var = PROVIDER_KEY_ENV.get(provider, "")
        if env_var:
            key = os.environ.get(env_var, "")
            if key:
                return key

        # Ollama doesn't need a key
        if provider == AIProvider.OLLAMA:
            return "local"

        return ""

    async def _dispatch(
        self,
        cfg: ModelConfig,
        api_key: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Route the call to the correct provider implementation."""
        provider = cfg.provider

        if provider == AIProvider.ANTHROPIC:
            if not api_key:
                raise ConfigError("Anthropic API key not configured")
            p = AnthropicProvider(api_key=api_key)
            return await p.call(messages, model_id=cfg.id, **kwargs)

        elif provider == AIProvider.GEMINI:
            if not api_key:
                raise ConfigError("Gemini API key not configured")
            p = GeminiProvider(api_key=api_key)
            return await p.call(messages, model_id=cfg.id, **kwargs)

        elif provider == AIProvider.OLLAMA:
            endpoint = cfg.endpoint or "http://localhost:11434/v1"
            p = OllamaProvider(base_url=endpoint, api_key=api_key)
            return await p.call(messages, model_id=cfg.id, **kwargs)

        else:
            # OpenAI-compatible: openai, deepseek, xai, qwen
            if not api_key or api_key == "local":
                raise ConfigError(
                    f"API key not configured for provider '{provider}'"
                )
            p = OpenAICompatProvider(
                api_key=api_key,
                base_url=cfg.endpoint,
                provider=provider,
            )
            return await p.call(messages, model_id=cfg.id, **kwargs)
