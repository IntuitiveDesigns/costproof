"""Python SDK for the local CostProof management API."""

from __future__ import annotations

from typing import Any

import httpx


class CostProofClient:
    """Small synchronous client for dashboard and spend APIs."""

    def __init__(
        self,
        base_url: str = "http://localhost:4001",
        *,
        api_key: str | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        """Return server health."""

        return self._get("/health")

    def spend_summary(self) -> dict[str, Any]:
        """Return aggregate local spend from the CostProof server."""

        return self._get("/api/spend/summary")

    def routing_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent audit-safe routing decisions."""

        payload = self._get(f"/api/routing/decisions?limit={limit}")
        decisions = payload.get("decisions", [])
        if not isinstance(decisions, list):
            return []
        return [decision for decision in decisions if isinstance(decision, dict)]

    def _get(self, path: str) -> dict[str, Any]:
        headers = {}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        response = httpx.get(
            f"{self.base_url}{path}",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("CostProof server returned an unexpected response shape")
        return data


__all__ = ["CostProofClient"]
