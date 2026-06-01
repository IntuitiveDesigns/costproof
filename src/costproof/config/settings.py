"""Runtime settings and YAML configuration models."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


FallbackPolicy = Literal["downroute", "reject", "warn"]
ProviderType = Literal["openai_compatible", "anthropic", "ollama", "mock"]


class CostProofConfigError(ValueError):
    """Raised when a CostProof YAML file cannot be loaded or validated."""


class CostProofSettings(BaseSettings):
    """Process-level settings.

    Values can be overridden with ``COSTPROOF_*`` environment variables.
    """

    model_config = SettingsConfigDict(env_prefix="COSTPROOF_")

    config_path: str = "examples/costproof.yaml"
    sqlite_path: str = "costproof.db"
    dry_run: bool = True
    log_prompt_content: bool = False


class BudgetConfig(BaseModel):
    """Hard and soft budget controls for the default project."""

    model_config = ConfigDict(extra="forbid")

    daily_limit_usd: float = Field(ge=0)
    monthly_limit_usd: float = Field(ge=0)
    warning_threshold: float = Field(default=0.8, ge=0, le=1)
    circuit_breaker_rate_usd_per_hour: float = Field(default=0, ge=0)


class EnterpriseConfig(BaseModel):
    """Enterprise adoption controls for tenancy, audit, and governance."""

    model_config = ConfigDict(extra="forbid")

    organization: str = "default-org"
    team: str = "default-team"
    require_project_header: bool = False
    require_team_header: bool = False
    require_organization_header: bool = False
    audit_log_enabled: bool = True
    prompt_logging_allowed: bool = False
    team_daily_limit_usd: float | None = Field(default=None, ge=0)
    organization_daily_limit_usd: float | None = Field(default=None, ge=0)


class ModelRoute(BaseModel):
    """A candidate model inside a routing tier."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)

    @property
    def pricing_key(self) -> str:
        """Return the canonical pricing-table lookup key."""

        return f"{self.provider}/{self.model}"


class RoutingTier(BaseModel):
    """A complexity tier and its allowed model candidates."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    complexity_max: float = Field(ge=0, le=1)
    models: list[ModelRoute] = Field(min_length=1)
    cost_cap_per_request_usd: float = Field(gt=0)


class RoutingConfig(BaseModel):
    """Model-ladder configuration."""

    model_config = ConfigDict(extra="forbid")

    fallback: FallbackPolicy = "downroute"
    tiers: list[RoutingTier] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_tiers(self) -> Self:
        """Ensure tier names and thresholds are deterministic."""

        names = [tier.name for tier in self.tiers]
        if len(names) != len(set(names)):
            raise ValueError("routing tier names must be unique")

        thresholds = [tier.complexity_max for tier in self.tiers]
        if thresholds != sorted(thresholds):
            raise ValueError("routing tiers must be ordered by complexity_max ascending")

        if self.tiers[-1].complexity_max < 1:
            raise ValueError("the last routing tier must cover complexity_max 1.0")

        return self


class EndpointOverride(BaseModel):
    """Per-application-endpoint routing override."""

    model_config = ConfigDict(extra="forbid")

    force_tier: str | None = None


class ProviderConfig(BaseModel):
    """Provider connection details.

    Secrets are referenced by environment variable name; secret values stay out of YAML.
    """

    model_config = ConfigDict(extra="forbid")

    type: ProviderType = "openai_compatible"
    base_url: str | None = None
    api_key_env: str | None = None
    enabled: bool = True
    timeout_seconds: float = Field(default=60, gt=0)
    headers: dict[str, str] = Field(default_factory=dict)


class ModelPricing(BaseModel):
    """Local model pricing used for pre-request estimates."""

    model_config = ConfigDict(extra="forbid")

    input_per_1m_usd: float = Field(ge=0)
    output_per_1m_usd: float = Field(ge=0)


class ProxyConfig(BaseModel):
    """Proxy behavior toggles."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool | None = None
    default_output_tokens: int = Field(default=512, ge=1)
    reject_streaming_when_forwarding: bool = True


def _default_providers() -> dict[str, ProviderConfig]:
    return {
        "openai": ProviderConfig(
            type="openai_compatible",
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        ),
        "ollama": ProviderConfig(
            type="openai_compatible",
            base_url="http://localhost:11434/v1",
            api_key_env=None,
        ),
    }


def _sample_pricing() -> dict[str, ModelPricing]:
    """Return sample pricing for the example configuration.

    Production deployments should maintain this table from approved vendor rates.
    """

    return {
        "openai/gpt-4o-mini": ModelPricing(input_per_1m_usd=0.15, output_per_1m_usd=0.60),
        "openai/gpt-4o": ModelPricing(input_per_1m_usd=5.00, output_per_1m_usd=15.00),
        "anthropic/claude-haiku-4-5": ModelPricing(
            input_per_1m_usd=0.80,
            output_per_1m_usd=4.00,
        ),
        "anthropic/claude-sonnet-4-6": ModelPricing(
            input_per_1m_usd=3.00,
            output_per_1m_usd=15.00,
        ),
        "anthropic/claude-opus-4-6": ModelPricing(
            input_per_1m_usd=15.00,
            output_per_1m_usd=75.00,
        ),
    }


class CostProofConfig(BaseModel):
    """Validated CostProof YAML configuration."""

    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1)
    budget: BudgetConfig
    routing: RoutingConfig
    endpoints: dict[str, EndpointOverride] = Field(default_factory=dict)
    enterprise: EnterpriseConfig = Field(default_factory=EnterpriseConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=_default_providers)
    pricing: dict[str, ModelPricing] = Field(default_factory=_sample_pricing)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        """Check cross-references that Pydantic cannot infer field-by-field."""

        tier_names = {tier.name for tier in self.routing.tiers}
        for endpoint, override in self.endpoints.items():
            if override.force_tier and override.force_tier not in tier_names:
                raise ValueError(f"endpoint {endpoint!r} references unknown tier {override.force_tier!r}")

        missing_providers: set[str] = set()
        missing_pricing: set[str] = set()
        for tier in self.routing.tiers:
            for route in tier.models:
                if route.provider not in self.providers:
                    missing_providers.add(route.provider)
                if route.pricing_key not in self.pricing:
                    missing_pricing.add(route.pricing_key)

        if missing_providers:
            providers = ", ".join(sorted(missing_providers))
            raise ValueError(f"routing references unknown providers: {providers}")

        if missing_pricing:
            pricing = ", ".join(sorted(missing_pricing))
            raise ValueError(f"pricing is missing for configured models: {pricing}")

        return self

    def tier_by_name(self, name: str) -> RoutingTier:
        """Return a configured routing tier by name."""

        for tier in self.routing.tiers:
            if tier.name == name:
                return tier
        raise KeyError(name)


def load_config(path: str | Path) -> CostProofConfig:
    """Load and validate a CostProof YAML configuration file."""

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CostProofConfigError(f"Could not read config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CostProofConfigError(f"Could not parse config {config_path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise CostProofConfigError(f"Config {config_path} must contain a YAML object")

    try:
        return CostProofConfig.model_validate(cast(dict[str, object], raw))
    except ValidationError as exc:
        raise CostProofConfigError(str(exc)) from exc
