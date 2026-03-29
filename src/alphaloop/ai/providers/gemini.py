"""
ai/providers/gemini.py
Async Gemini provider using httpx against the Google Generative Language API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from alphaloop.core.errors import AlphaLoopError

logger = logging.getLogger(__name__)

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Translate legacy model names to current ones
_ALIASES: dict[str, str] = {
    "gemini-1.5-flash": "gemini-2.0-flash",
    "gemini-1.5-flash-latest": "gemini-2.0-flash",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-latest": "gemini-2.5-pro",
}


class GeminiProvider:
    """Async provider for the Google Gemini (Generative Language) API."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise AlphaLoopError("Gemini API key is required")
        self._api_key = api_key

    async def call(
        self,
        messages: list[dict[str, str]],
        model_id: str = "gemini-2.5-flash",
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        system: str | None = None,
        timeout: float = 60.0,
        response_mime_type: str = "application/json",
        **kwargs: Any,
    ) -> str:
        """
        Send a generateContent request to Gemini and return the text.

        Parameters
        ----------
        messages : list of dicts
            Chat messages with "role" and "content" keys.
            Gemini roles: "user" and "model".
        model_id : str
            Model identifier (aliases resolved automatically).
        max_tokens : int
            Max output tokens.
        temperature : float
            Sampling temperature.
        system : str or None
            System instruction.
        timeout : float
            HTTP timeout in seconds.
        response_mime_type : str
            Response MIME type (default JSON).
        """
        model_id = _ALIASES.get(model_id, model_id)
        url = f"{_API_BASE}/{model_id}:generateContent"

        # Convert messages to Gemini "contents" format
        contents: list[dict[str, Any]] = []
        for msg in messages:
            role = msg["role"]
            # Map standard roles to Gemini roles
            if role == "assistant":
                role = "model"
            elif role == "system":
                # System messages handled via systemInstruction
                continue
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}],
            })

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": response_mime_type,
            },
        }

        # Collect system text from explicit param + any system-role messages
        system_parts: list[str] = []
        if system:
            system_parts.append(system)
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
        if system_parts:
            body["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"x-goog-api-key": self._api_key},
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = resp.text
                raise type(e)(f"{e}: {body}", request=e.request, response=e.response) from e
            data = resp.json()

        # Extract text from first candidate
        candidates = data.get("candidates", [])
        if not candidates:
            raise AlphaLoopError(
                f"Gemini returned no candidates for model {model_id}"
            )

        parts = candidates[0].get("content", {}).get("parts", [])
        text = parts[0].get("text", "") if parts else ""

        usage = data.get("usageMetadata", {})
        logger.debug(
            "[gemini] %s — in=%d out=%d",
            model_id,
            usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0),
        )

        return text
