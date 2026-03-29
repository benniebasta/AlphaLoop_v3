"""
ai/providers/anthropic.py
Async Anthropic (Claude) provider using httpx.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from alphaloop.core.errors import AlphaLoopError

logger = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicProvider:
    """Async provider for the Anthropic Messages API."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise AlphaLoopError("Anthropic API key is required")
        self._api_key = api_key

    async def call(
        self,
        messages: list[dict[str, str]],
        model_id: str = "claude-sonnet-4-6",
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        system: str | None = None,
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> str:
        """
        Send a chat request to Anthropic and return the text response.

        Parameters
        ----------
        messages : list of dicts
            Each dict has "role" and "content" keys.
        model_id : str
            The model to use.
        max_tokens : int
            Maximum tokens in the response.
        temperature : float
            Sampling temperature.
        system : str or None
            Optional system prompt.
        timeout : float
            HTTP request timeout in seconds.

        Returns
        -------
        str
            The text content of the first response block.
        """
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        # Forward any extra kwargs (e.g. top_p, stop_sequences)
        for k, v in kwargs.items():
            if k not in body:
                body[k] = v

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(_API_URL, headers=headers, json=body)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = resp.text
                raise type(e)(f"{e}: {body}", request=e.request, response=e.response) from e
            data = resp.json()

        # Extract text from first content block
        content_blocks = data.get("content", [])
        if not content_blocks:
            raise AlphaLoopError(
                f"Anthropic returned empty content for model {model_id}"
            )

        text = content_blocks[0].get("text", "")

        usage = data.get("usage", {})
        logger.debug(
            "[anthropic] %s — in=%d out=%d",
            model_id,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )

        return text
