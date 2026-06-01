"""Cost estimation for routed requests."""

from __future__ import annotations

from collections.abc import Mapping

from costproof.config.settings import ModelPricing
from costproof.router.models import CostEstimate
from costproof.router.scoring import approximate_token_count


class UnknownPricingError(KeyError):
    """Raised when a model is missing from the local pricing table."""


class CostEstimator:
    """Estimate pre-request cost from prompt text and configured pricing."""

    def __init__(self, pricing: Mapping[str, ModelPricing], default_output_tokens: int) -> None:
        self._pricing = pricing
        self._default_output_tokens = default_output_tokens

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        prompt_text: str,
        payload: Mapping[str, object],
    ) -> CostEstimate:
        """Estimate input/output tokens and cost for a provider model."""

        pricing = self._lookup_pricing(provider, model)
        input_tokens = approximate_token_count(prompt_text)
        output_tokens = self._output_token_budget(payload)
        input_cost = input_tokens / 1_000_000 * pricing.input_per_1m_usd
        output_cost = output_tokens / 1_000_000 * pricing.output_per_1m_usd
        return CostEstimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=round(input_cost, 8),
            output_cost_usd=round(output_cost, 8),
        )

    def _lookup_pricing(self, provider: str, model: str) -> ModelPricing:
        key = f"{provider}/{model}"
        pricing = self._pricing.get(key)
        if pricing is None:
            pricing = self._pricing.get(model)
        if pricing is None:
            raise UnknownPricingError(key)
        return pricing

    def _output_token_budget(self, payload: Mapping[str, object]) -> int:
        for key in ("max_completion_tokens", "max_tokens"):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, float) and value > 0:
                return int(value)
        return self._default_output_tokens
