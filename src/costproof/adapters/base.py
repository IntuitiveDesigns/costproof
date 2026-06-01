"""Provider adapter contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from costproof.config.settings import ProviderConfig


class ProviderAdapterError(RuntimeError):
    """Raised when a provider adapter cannot complete a request."""


class ProviderAdapter(Protocol):
    """Provider adapter contract."""

    name: str

    async def chat_completions(
        self,
        payload: Mapping[str, Any],
        provider_config: ProviderConfig,
    ) -> dict[str, Any]:
        """Send a chat-completions request to the provider."""
        ...
