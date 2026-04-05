"""
Unit tests for the AI caller, model hub, rate limiter, and providers.

All provider HTTP calls are mocked — no real API keys or network needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alphaloop.ai.caller import AICaller
from alphaloop.ai.model_hub import (
    BUILTIN_MODELS,
    DEFAULT_ROLES,
    ModelConfig,
    get_model_by_id,
    get_models_by_provider,
    get_models_for_role,
    register_model,
    resolve_role,
)
from alphaloop.ai.rate_limiter import AsyncRateLimiter
from alphaloop.core.errors import AlphaLoopError, ConfigError, RateLimitError
from alphaloop.core.types import AIProvider


# ── ModelHub tests ────────────────────────────────────────────────────────────


class TestModelHub:
    def test_builtin_models_not_empty(self):
        assert len(BUILTIN_MODELS) > 0

    def test_get_model_by_id_found(self):
        cfg = get_model_by_id("gemini-2.5-flash")
        assert cfg is not None
        assert cfg.provider == AIProvider.GEMINI
        assert cfg.display_name == "Gemini 2.5 Flash"

    def test_get_model_by_id_not_found(self):
        assert get_model_by_id("nonexistent-model-xyz") is None

    def test_get_models_for_role(self):
        signal_models = get_models_for_role("signal")
        assert len(signal_models) > 0
        for m in signal_models:
            assert "signal" in m.roles

    def test_get_models_by_provider(self):
        anthropic_models = get_models_by_provider(AIProvider.ANTHROPIC)
        assert len(anthropic_models) >= 2  # sonnet + haiku at minimum
        for m in anthropic_models:
            assert m.provider == AIProvider.ANTHROPIC

    def test_resolve_role_signal(self):
        cfg = resolve_role("signal")
        assert cfg is not None
        assert cfg.id == DEFAULT_ROLES["signal"]

    def test_resolve_role_fallback(self):
        cfg = resolve_role("fallback")
        assert cfg is not None
        assert cfg.id == DEFAULT_ROLES["fallback"]

    def test_resolve_role_unknown(self):
        cfg = resolve_role("nonexistent_role")
        assert cfg is None

    def test_model_config_fields(self):
        cfg = get_model_by_id("claude-sonnet-4-6")
        assert cfg is not None
        assert cfg.id == "claude-sonnet-4-6"
        assert cfg.provider == AIProvider.ANTHROPIC
        assert cfg.context_window == 200_000
        assert cfg.max_output == 8_192
        assert isinstance(cfg.roles, list)
        assert cfg.cost_tier >= 0

    def test_register_custom_model(self):
        custom = ModelConfig(
            id="my-custom-llama:70b",
            provider=AIProvider.OLLAMA,
            display_name="Custom Llama 70B",
            roles=["signal"],
            cost_tier=0,
            endpoint="http://localhost:11434/v1",
            enabled=True,
        )
        register_model(custom)
        found = get_model_by_id("my-custom-llama:70b")
        assert found is not None
        assert found.display_name == "Custom Llama 70B"

    def test_all_providers_covered(self):
        """Every AIProvider enum value has at least one model in the catalog."""
        providers_in_catalog = {m.provider for m in BUILTIN_MODELS}
        for p in AIProvider:
            assert p in providers_in_catalog, f"No model for provider {p}"


# ── RateLimiter tests ─────────────────────────────────────────────────────────


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_within_limit(self):
        limiter = AsyncRateLimiter(max_calls=3, window_seconds=60.0)
        # Should not raise for 3 calls
        for _ in range(3):
            await limiter.acquire("test-provider")

    @pytest.mark.asyncio
    async def test_acquire_exceeds_limit(self):
        limiter = AsyncRateLimiter(max_calls=2, window_seconds=60.0)
        await limiter.acquire("test-provider")
        await limiter.acquire("test-provider")
        with pytest.raises(RateLimitError):
            await limiter.acquire("test-provider")

    @pytest.mark.asyncio
    async def test_separate_providers(self):
        limiter = AsyncRateLimiter(max_calls=1, window_seconds=60.0)
        await limiter.acquire("provider-a")
        await limiter.acquire("provider-b")  # Different provider, should work
        with pytest.raises(RateLimitError):
            await limiter.acquire("provider-a")  # Same provider, should fail

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        limiter = AsyncRateLimiter(max_calls=1, window_seconds=60.0)
        await limiter.acquire("provider-a")
        limiter.reset("provider-a")
        # Should work again after reset
        await limiter.acquire("provider-a")

    @pytest.mark.asyncio
    async def test_reset_all(self):
        limiter = AsyncRateLimiter(max_calls=1, window_seconds=60.0)
        await limiter.acquire("provider-a")
        await limiter.acquire("provider-b")
        limiter.reset()
        await limiter.acquire("provider-a")
        await limiter.acquire("provider-b")


# ── AICaller tests ────────────────────────────────────────────────────────────


def _mock_httpx_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


class TestAICaller:
    @pytest.mark.asyncio
    async def test_aicaller_is_async_callable(self):
        caller = AICaller(api_keys={"gemini": "test-key"})

        with patch.object(caller, "call_model", AsyncMock(return_value="ok")) as mock_call:
            result = await caller("gemini-2.5-flash", [{"role": "user", "content": "hi"}])

        assert result == "ok"
        mock_call.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_model_unknown_raises(self):
        caller = AICaller(api_keys={"anthropic": "test-key"})
        with pytest.raises(ConfigError, match="not found"):
            await caller.call_model(
                "nonexistent-model", [{"role": "user", "content": "hi"}]
            )

    @pytest.mark.asyncio
    async def test_call_model_disabled_raises(self):
        caller = AICaller(api_keys={})
        disabled = get_model_by_id("claude-sonnet-4-6").model_copy(update={"enabled": False})
        with patch("alphaloop.ai.caller.get_model_by_id", return_value=disabled):
            with pytest.raises(ConfigError, match="disabled"):
                await caller.call_model(
                    "claude-sonnet-4-6", [{"role": "user", "content": "hi"}]
                )

    @pytest.mark.asyncio
    async def test_call_model_no_key_raises(self):
        caller = AICaller(api_keys={})
        with pytest.raises((ConfigError, AlphaLoopError)):
            await caller.call_model(
                "claude-sonnet-4-6", [{"role": "user", "content": "hi"}]
            )

    @pytest.mark.asyncio
    async def test_call_model_anthropic(self):
        """Mock the httpx post to simulate Anthropic response."""
        mock_response = _mock_httpx_response(
            {
                "content": [{"type": "text", "text": "Hello from Claude!"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )

        caller = AICaller(api_keys={"anthropic": "test-key-123"})

        with patch("alphaloop.ai.providers.anthropic.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await caller.call_model(
                "claude-sonnet-4-6",
                [{"role": "user", "content": "test"}],
                system="You are helpful.",
            )

        assert result == "Hello from Claude!"

    @pytest.mark.asyncio
    async def test_call_model_gemini(self):
        """Mock the httpx post to simulate Gemini response."""
        mock_response = _mock_httpx_response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": '{"signal": "buy"}'}],
                            "role": "model",
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 15,
                    "candidatesTokenCount": 8,
                },
            }
        )

        caller = AICaller(api_keys={"gemini": "test-gemini-key"})

        with patch("alphaloop.ai.providers.gemini.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await caller.call_model(
                "gemini-2.5-flash",
                [{"role": "user", "content": "analyze EURUSD"}],
            )

        assert result == '{"signal": "buy"}'

    @pytest.mark.asyncio
    async def test_call_model_openai_compat(self):
        """Mock the httpx post to simulate OpenAI-compatible response."""
        mock_response = _mock_httpx_response(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "GPT says hi"}}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }
        )

        caller = AICaller(api_keys={"openai": "test-openai-key"})

        with patch(
            "alphaloop.ai.providers.openai_compat.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await caller.call_model(
                "gpt-4o-mini",
                [{"role": "user", "content": "hello"}],
            )

        assert result == "GPT says hi"

    @pytest.mark.asyncio
    async def test_call_model_deepseek(self):
        """DeepSeek uses OpenAI-compat provider."""
        mock_response = _mock_httpx_response(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "DeepSeek response"}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            }
        )

        caller = AICaller(api_keys={"deepseek": "test-ds-key"})

        with patch(
            "alphaloop.ai.providers.openai_compat.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await caller.call_model(
                "deepseek-chat",
                [{"role": "user", "content": "hello"}],
            )

        assert result == "DeepSeek response"

    @pytest.mark.asyncio
    async def test_call_role(self):
        """call_role resolves default role -> model_id -> provider."""
        mock_response = _mock_httpx_response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "signal result"}],
                            "role": "model",
                        }
                    }
                ],
                "usageMetadata": {},
            }
        )

        caller = AICaller(api_keys={"gemini": "test-key"})

        with patch("alphaloop.ai.providers.gemini.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await caller.call_role(
                "signal",
                [{"role": "user", "content": "analyze"}],
            )

        assert result == "signal result"

    @pytest.mark.asyncio
    async def test_rate_limit_blocks(self):
        """Caller should raise RateLimitError when limit is exceeded."""
        limiter = AsyncRateLimiter(max_calls=1, window_seconds=60.0)
        caller = AICaller(
            api_keys={"gemini": "key"},
            rate_limiter=limiter,
        )

        mock_response = _mock_httpx_response(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}], "role": "model"}}
                ],
                "usageMetadata": {},
            }
        )

        with patch("alphaloop.ai.providers.gemini.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            # First call should succeed
            await caller.call_model(
                "gemini-2.5-flash", [{"role": "user", "content": "1"}]
            )

            # Second call should be rate limited
            with pytest.raises(RateLimitError):
                await caller.call_model(
                    "gemini-2.5-flash", [{"role": "user", "content": "2"}]
                )

    @pytest.mark.asyncio
    async def test_call_model_hard_times_out(self):
        caller = AICaller(api_keys={"gemini": "test-key"})

        async def _hang(**kwargs):
            await asyncio.sleep(1.0)
            return "never"

        with patch.object(caller, "_dispatch", side_effect=_hang):
            with pytest.raises(AlphaLoopError, match="timed out"):
                await caller.call_model(
                    "gemini-2.5-flash",
                    [{"role": "user", "content": "hi"}],
                    timeout=0.01,
                    max_retries=0,
                )
