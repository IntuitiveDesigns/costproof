"""Cost estimation for routed requests."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping

from costproof.config.settings import ModelPricing
from costproof.router.models import CostEstimate
from costproof.router.scoring import approximate_token_count

_log = logging.getLogger(__name__)


class UnknownPricingError(KeyError):
    """Raised when a model is missing from the local pricing table."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.pricing_key = f"{provider}/{model}"
        super().__init__(self.pricing_key)

    def __str__(self) -> str:
        return (
            f"missing pricing for provider={self.provider!r}, model={self.model!r} "
            f"(expected key {self.pricing_key!r})"
        )


class InvalidTokenBudgetError(ValueError):
    """Raised when a request contains an invalid output-token budget."""


class CostEstimator:
    """Estimate pre-request cost from prompt text and configured pricing."""

    def __init__(
        self,
        pricing: Mapping[str, ModelPricing],
        default_output_tokens: int,
        output_token_utilization_factor: float = 0.5,
    ) -> None:
        if default_output_tokens < 1:
            raise ValueError("default_output_tokens must be at least 1")
        if output_token_utilization_factor <= 0 or output_token_utilization_factor > 1:
            raise ValueError("output_token_utilization_factor must be greater than 0 and at most 1")
        self._validate_pricing(pricing)
        self._pricing = pricing
        self._default_output_tokens = default_output_tokens
        self._output_token_utilization_factor = output_token_utilization_factor

    def estimate(
        self,
        *,
        provider: str,
        model: str,
        prompt_text: str,
        payload: Mapping[str, object],
        input_tokens: int | None = None,
    ) -> CostEstimate:
        """Estimate input/output tokens and cost for a provider model."""

        pricing = self._lookup_pricing(provider, model)
        if input_tokens is None:
            input_tokens = approximate_token_count(prompt_text)
        elif input_tokens < 0:
            raise ValueError("input_tokens must be non-negative")
        output_tokens = self._estimated_output_tokens(payload)
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
            if pricing is not None:
                _log.warning(
                    "Pricing fallback: no entry for %r, using bare model key %r",
                    key,
                    model,
                )
        if pricing is None:
            raise UnknownPricingError(provider, model)
        return pricing

    def _estimated_output_tokens(self, payload: Mapping[str, object]) -> int:
        ceiling = self._output_token_ceiling(payload)
        return max(1, math.ceil(ceiling * self._output_token_utilization_factor))

    def _output_token_ceiling(self, payload: Mapping[str, object]) -> int:
        for key in ("max_completion_tokens", "max_tokens"):
            value = payload.get(key)
            if value is not None:
                return self._coerce_token_ceiling(key, value)
        return self._default_output_tokens

    def _coerce_token_ceiling(self, key: str, value: object) -> int:
        if isinstance(value, bool):
            raise InvalidTokenBudgetError(f"{key} must be a positive integer, not a boolean")
        if isinstance(value, int):
            if value <= 0:
                raise InvalidTokenBudgetError(f"{key} must be a positive integer")
            return value
        if isinstance(value, float):
            if value <= 0 or not value.is_integer():
                raise InvalidTokenBudgetError(f"{key} must be a positive integer, got {value!r}")
            return int(value)
        raise InvalidTokenBudgetError(f"{key} must be a positive integer, got {type(value).__name__}")

    def _validate_pricing(self, pricing: Mapping[str, ModelPricing]) -> None:
        for key, value in pricing.items():
            if value.input_per_1m_usd < 0 or value.output_per_1m_usd < 0:
                raise ValueError(f"pricing for {key!r} must be non-negative")
