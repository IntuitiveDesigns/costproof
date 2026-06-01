"""Routing domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal


DecisionStatus = Literal["accepted", "blocked", "completed", "failed"]
PolicyAction = Literal["allow", "warn", "block"]


@dataclass(frozen=True)
class RequestContext:
    """Enterprise tenancy and attribution context for a request."""

    organization: str
    team: str
    project: str
    endpoint: str


@dataclass(frozen=True)
class ComplexityResult:
    """Rule-based complexity score and explainable signals."""

    score: float
    prompt_tokens: int
    signals: tuple[str, ...]


@dataclass(frozen=True)
class CostEstimate:
    """Pre-request token and dollar estimate."""

    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float

    @property
    def total_tokens(self) -> int:
        """Return the total estimated token count."""

        return self.input_tokens + self.output_tokens

    @property
    def total_cost_usd(self) -> float:
        """Return the total estimated request cost."""

        return self.input_cost_usd + self.output_cost_usd


@dataclass(frozen=True)
class RoutingDecision:
    """Audit-safe routing decision provenance."""

    request_id: str
    timestamp: datetime
    organization: str
    team: str
    project: str
    endpoint: str
    requested_model: str | None
    selected_tier: str
    selected_provider: str
    selected_model: str
    complexity_score: float
    complexity_signals: tuple[str, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    cost_cap_usd: float
    fallback_applied: bool
    reason: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dictionary without prompt content."""

        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass(frozen=True)
class BudgetScopeStatus:
    """Budget projection for one governance scope."""

    scope: str
    name: str
    period: str
    limit_usd: float
    current_spend_usd: float
    projected_spend_usd: float
    warning_threshold_usd: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dictionary."""

        return asdict(self)


@dataclass(frozen=True)
class BudgetEvaluation:
    """Budget and circuit-breaker result for a routing decision."""

    allowed: bool
    action: PolicyAction
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    scopes: tuple[BudgetScopeStatus, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dictionary."""

        return {
            "allowed": self.allowed,
            "action": self.action,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "scopes": [scope.to_dict() for scope in self.scopes],
        }
