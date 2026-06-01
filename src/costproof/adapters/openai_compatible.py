"""Adapter for OpenAI-compatible chat-completions providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from costproof.adapters.base import ProviderAdapterError
from costproof.config.settings import ProviderConfig


class OpenAICompatibleAdapter:
    """Forward requests to providers that implement OpenAI's chat API shape."""

    name = "openai_compatible"

    async def chat_completions(
        self,
        payload: Mapping[str, Any],
        provider_config: ProviderConfig,
    ) -> dict[str, Any]:
        """Forward a non-streaming chat-completions request."""

        if not provider_config.base_url:
            raise ProviderAdapterError("provider base_url is not configured")

        headers = {"content-type": "application/json", **provider_config.headers}
        if provider_config.api_key_env:
            api_key = os.getenv(provider_config.api_key_env)
            if not api_key:
                raise ProviderAdapterError(
                    f"missing provider API key environment variable {provider_config.api_key_env}"
                )
            headers["authorization"] = f"Bearer {api_key}"

        url = f"{provider_config.base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=provider_config.timeout_seconds) as client:
            response = await client.post(url, json=dict(payload), headers=headers)

        if response.status_code >= 400:
            raise ProviderAdapterError(
                f"provider returned HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderAdapterError("provider returned non-JSON response") from exc

        if not isinstance(data, dict):
            raise ProviderAdapterError("provider returned an unexpected response shape")
        return data
