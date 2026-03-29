"""
ai/providers/openai_compat.py
Async OpenAI-compatible provider (OpenAI, DeepSeek, xAI, Qwen, Ollama).

All these providers expose an OpenAI-compatible /v1/chat/completions endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from alphaloop.core.errors import AlphaLoopError
from alphaloop.core.types import AIProvider

logger = logging.getLogger(__name__)

# Default base URLs per provider
DEFAULT_ENDPOINTS: dict[str, str] = {
    AIProvider.OPENAI: "https://api.openai.com/v1",
    AIProvider.DEEPSEEK: "https://api.deepseek.com/v1",
    AIProvider.XAI: "https://api.x.ai/v1",
    AIProvider.QWEN: "https://api.together.ai/v1",
    AIProvider.OLLAMA: "http://localhost:11434/v1",
}


class OpenAICompatProvider:
    """
    Async provider for any OpenAI-compatible chat completions API.

    Supports: OpenAI, DeepSeek, xAI/Grok, Qwen (via Together.ai), Ollama.
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        provider: str = AIProvider.OPENAI,
    ) -> None:
        self._api_key = api_key or "local"
        self._base_url = (
            base_url.rstrip("/")
            if base_url
            else DEFAULT_ENDPOINTS.get(provider, DEFAULT_ENDPOINTS[AIProvider.OPENAI])
        )
        self._provider = provider

    async def call(
        self,
        messages: list[dict[str, str]],
        model_id: str = "gpt-4o-mini",
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        system: str | None = None,
        timeout: float = 60.0,
        json_mode: bool = True,
        **kwargs: Any,
    ) -> str:
        """
        Send a chat completion request and return the response text.

        Parameters
        ----------
        messages : list of dicts
            Chat messages with "role" and "content" keys.
        model_id : str
            Model identifier.
        max_tokens : int
            Maximum tokens in the response.
        temperature : float
            Sampling temperature.
        system : str or None
            If provided, prepended as a system message.
        timeout : float
            HTTP timeout in seconds.
        json_mode : bool
            If True, request JSON output format.
        """
        url = f"{self._base_url}/chat/completions"

        # Build message list, prepending system if supplied
        final_messages: list[dict[str, str]] = []
        if system:
            final_messages.append({"role": "system", "content": system})
        final_messages.extend(messages)

        headers: dict[str, str] = {
            "content-type": "application/json",
        }
        if self._api_key and self._api_key != "local":
            headers["authorization"] = f"Bearer {self._api_key}"

        body: dict[str, Any] = {
            "model": model_id,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": final_messages,
        }

        if json_mode:
            body["response_format"] = {"type": "json_object"}

        # Forward extra kwargs
        for k, v in kwargs.items():
            if k not in body:
                body[k] = v

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = resp.text
                raise type(e)(f"{e}: {body}", request=e.request, response=e.response) from e
            data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            raise AlphaLoopError(
                f"OpenAI-compat ({self._provider}) returned no choices "
                f"for model {model_id}"
            )

        text = choices[0].get("message", {}).get("content", "")

        usage = data.get("usage", {})
        logger.debug(
            "[openai-compat/%s] %s — in=%d out=%d",
            self._provider,
            model_id,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )

        return text
