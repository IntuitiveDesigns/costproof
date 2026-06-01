"""Routing, cap enforcement, and budget policy evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from costproof.config.settings import CostProofConfig, ModelRoute, RoutingTier
from costproof.router.estimator import CostEstimator
from costproof.router.models import (
    BudgetEvaluation,
    BudgetScopeStatus,
    CostEstimate,
    RequestContext,
    RoutingDecision,
)
from costproof.router.scoring import ComplexityScorer, extract_prompt_text
from costproof.storage.sqlite import SQLiteAuditStore


class RoutingEngine:
    """Enterprise-aware model router."""

    def __init__(
        self,
        config: CostProofConfig,
        store: SQLiteAuditStore | None = None,
        scorer: ComplexityScorer | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.scorer = scorer or ComplexityScorer()
        self.estimator = CostEstimator(
            config.pricing,
            default_output_tokens=config.proxy.default_output_tokens,
        )

    def route(
        self,
        payload: Mapping[str, object],
        context: RequestContext,
    ) -> tuple[RoutingDecision, BudgetEvaluation]:
        """Return a routing decision and budget evaluation."""

        prompt_text = extract_prompt_text(payload)
        complexity = self.scorer.score_text(prompt_text)
        model_value = payload.get("model")
        requested_model = model_value if isinstance(model_value, str) else None
        tier, reason = self._select_tier(complexity.score, context.endpoint)
        tier_index = self.config.routing.tiers.index(tier)
        route = tier.models[0]
        estimate = self.estimator.estimate(
            provider=route.provider,
            model=route.model,
            prompt_text=prompt_text,
            payload=payload,
        )

        fallback_applied = False
        warnings: list[str] = []
        cap_block_reasons: list[str] = []

        if estimate.total_cost_usd > tier.cost_cap_per_request_usd:
            fallback = self.config.routing.fallback
            if fallback == "downroute" and tier_index > 0:
                lower = self._find_downroute(tier_index, prompt_text, payload)
                if lower is not None:
                    tier, route, estimate = lower
                    fallback_applied = True
                    reason = (
                        "downrouted because estimated cost exceeded the selected tier "
                        "per-request cap"
                    )
                else:
                    cap_block_reasons.append(
                        "estimated request cost exceeds every lower tier per-request cap"
                    )
            elif fallback == "warn":
                warnings.append(
                    "estimated request cost exceeds selected tier cap but fallback policy is warn"
                )
            else:
                cap_block_reasons.append("estimated request cost exceeds selected tier cap")

        decision = RoutingDecision(
            request_id=f"cp_{uuid4().hex}",
            timestamp=datetime.now(UTC),
            organization=context.organization,
            team=context.team,
            project=context.project,
            endpoint=context.endpoint,
            requested_model=requested_model,
            selected_tier=tier.name,
            selected_provider=route.provider,
            selected_model=route.model,
            complexity_score=complexity.score,
            complexity_signals=complexity.signals,
            estimated_input_tokens=estimate.input_tokens,
            estimated_output_tokens=estimate.output_tokens,
            estimated_cost_usd=round(estimate.total_cost_usd, 8),
            cost_cap_usd=tier.cost_cap_per_request_usd,
            fallback_applied=fallback_applied,
            reason=reason,
            warnings=tuple(warnings),
        )
        policy = self._evaluate_budget(context, decision, tuple(cap_block_reasons))
        return decision, policy

    def _select_tier(self, complexity_score: float, endpoint: str) -> tuple[RoutingTier, str]:
        override = self.config.endpoints.get(endpoint)
        if override and override.force_tier:
            tier = self.config.tier_by_name(override.force_tier)
            return tier, f"endpoint override forced tier {tier.name}"

        for tier in self.config.routing.tiers:
            if complexity_score <= tier.complexity_max:
                return tier, f"complexity score {complexity_score:.4f} matched tier {tier.name}"

        tier = self.config.routing.tiers[-1]
        return tier, f"complexity score {complexity_score:.4f} matched final tier {tier.name}"

    def _find_downroute(
        self,
        selected_tier_index: int,
        prompt_text: str,
        payload: Mapping[str, object],
    ) -> tuple[RoutingTier, ModelRoute, CostEstimate] | None:
        for tier in reversed(self.config.routing.tiers[:selected_tier_index]):
            route = tier.models[0]
            estimate = self.estimator.estimate(
                provider=route.provider,
                model=route.model,
                prompt_text=prompt_text,
                payload=payload,
            )
            if estimate.total_cost_usd <= tier.cost_cap_per_request_usd:
                return tier, route, estimate
        return None

    def _evaluate_budget(
        self,
        context: RequestContext,
        decision: RoutingDecision,
        cap_block_reasons: tuple[str, ...],
    ) -> BudgetEvaluation:
        now = decision.timestamp
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        hour_start = now - timedelta(hours=1)

        scopes = [
            self._scope_status(
                context=context,
                scope="project",
                name=context.project,
                period="day",
                limit=self.config.budget.daily_limit_usd,
                since=day_start,
                estimate=decision.estimated_cost_usd,
            ),
            self._scope_status(
                context=context,
                scope="project",
                name=context.project,
                period="month",
                limit=self.config.budget.monthly_limit_usd,
                since=month_start,
                estimate=decision.estimated_cost_usd,
            ),
        ]

        if self.config.budget.circuit_breaker_rate_usd_per_hour > 0:
            scopes.append(
                self._scope_status(
                    context=context,
                    scope="organization",
                    name=context.organization,
                    period="hour",
                    limit=self.config.budget.circuit_breaker_rate_usd_per_hour,
                    since=hour_start,
                    estimate=decision.estimated_cost_usd,
                )
            )

        enterprise = self.config.enterprise
        if enterprise.team_daily_limit_usd is not None:
            scopes.append(
                self._scope_status(
                    context=context,
                    scope="team",
                    name=context.team,
                    period="day",
                    limit=enterprise.team_daily_limit_usd,
                    since=day_start,
                    estimate=decision.estimated_cost_usd,
                )
            )
        if enterprise.organization_daily_limit_usd is not None:
            scopes.append(
                self._scope_status(
                    context=context,
                    scope="organization",
                    name=context.organization,
                    period="day",
                    limit=enterprise.organization_daily_limit_usd,
                    since=day_start,
                    estimate=decision.estimated_cost_usd,
                )
            )

        reasons = list(cap_block_reasons)
        warnings: list[str] = []
        threshold = self.config.budget.warning_threshold

        for scope in scopes:
            if scope.projected_spend_usd > scope.limit_usd:
                reasons.append(
                    f"{scope.scope} {scope.name} {scope.period} budget would be exceeded "
                    f"({scope.projected_spend_usd:.6f} > {scope.limit_usd:.6f})"
                )
            elif threshold > 0 and scope.projected_spend_usd >= scope.warning_threshold_usd:
                warnings.append(
                    f"{scope.scope} {scope.name} {scope.period} spend is above warning threshold"
                )

        if reasons:
            return BudgetEvaluation(
                allowed=False,
                action="block",
                reasons=tuple(reasons),
                warnings=tuple(warnings),
                scopes=tuple(scopes),
            )

        return BudgetEvaluation(
            allowed=True,
            action="warn" if warnings else "allow",
            reasons=(),
            warnings=tuple(warnings),
            scopes=tuple(scopes),
        )

    def _scope_status(
        self,
        *,
        context: RequestContext,
        scope: str,
        name: str,
        period: str,
        limit: float,
        since: datetime,
        estimate: float,
    ) -> BudgetScopeStatus:
        current_spend = 0.0
        if self.store is not None:
            current_spend = self.store.spend_since(context=context, scope=scope, since=since)
        projected = round(current_spend + estimate, 8)
        return BudgetScopeStatus(
            scope=scope,
            name=name,
            period=period,
            limit_usd=limit,
            current_spend_usd=round(current_spend, 8),
            projected_spend_usd=projected,
            warning_threshold_usd=round(limit * self.config.budget.warning_threshold, 8),
        )
