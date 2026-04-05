"""
ai/providers/ollama.py
Async Ollama provider — thin wrapper over OpenAICompatProvider with Ollama defaults.
"""

from __future__ import annotations

from typing import Any

from alphaloop.ai.providers.openai_compat import OpenAICompatProvider
from alphaloop.core.types import AIProvider


class OllamaProvider:
    """
    Async provider for local Ollama models.

    Delegates to OpenAICompatProvider with Ollama's default endpoint
    and no auth required.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "local",
    ) -> None:
        self._inner = OpenAICompatProvider(
            api_key=api_key or "local",
            base_url=base_url,
            provider=AIProvider.OLLAMA,
        )

    async def call(
        self,
        messages: list[dict[str, str]],
        model_id: str = "qwen2.5:7b",
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        system: str | None = None,
        timeout: float = 120.0,
        json_mode: bool = True,
        **kwargs: Any,
    ) -> str:
        """
        Send a chat request to a local Ollama instance.

        Parameters match OpenAICompatProvider.call(); the only difference
        is a longer default timeout (local models can be slower).
        """
        return await self._inner.call(
            messages=messages,
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            timeout=timeout,
            json_mode=json_mode,
            **kwargs,
        )
