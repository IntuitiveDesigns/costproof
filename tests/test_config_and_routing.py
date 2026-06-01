"""Enterprise routing and budget behavior tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import LogCaptureFixture, MonkeyPatch, raises

import costproof.proxy.app as proxy_app
import costproof.server.app as server_app
from costproof import __version__
from costproof.config import load_config
from costproof.config.settings import CostProofConfig, ModelPricing
from costproof.router.estimator import CostEstimator, InvalidTokenBudgetError, UnknownPricingError
from costproof.router.engine import RoutingConfigurationError, RoutingEngine
from costproof.router.models import RequestContext
from costproof.router.scoring import ComplexityScorer, approximate_token_count, extract_prompt_text
from costproof.storage import SQLiteAuditStore


def test_version_is_defined() -> None:
    assert __version__


def test_example_config_loads_enterprise_controls() -> None:
    config = load_config("examples/costproof.yaml")

    assert config.enterprise.audit_log_enabled is True
    assert config.enterprise.prompt_logging_allowed is False
    assert config.providers["openai"].api_key_env == "OPENAI_API_KEY"
    assert config.tier_by_name("simple").models[0].model == "gpt-4o-mini"


def test_complexity_scorer_is_explainable_and_ordered() -> None:
    scorer = ComplexityScorer()

    simple = scorer.score_text("Classify this review as positive or negative: loved it.")
    complex_result = scorer.score_text(
        """
        Debug this Python function and return JSON:
        ```python
        def broken(items):
            return {item.id: item.value for item in items}
        ```
        1. Explain the root cause.
        2. Compare two fixes.
        3. Include compliance tradeoffs.
        """
    )

    assert complex_result.score > simple.score
    assert "code_detected" in complex_result.signals
    assert "structured_output" in complex_result.signals


def test_code_token_estimate_accounts_for_punctuation() -> None:
    prose = "Please check this value before returning the answer."
    code = "if x != None and y >= 0:\n    return foo_bar(x) + 1\n"

    assert approximate_token_count(code) > approximate_token_count(prose)
    assert approximate_token_count(code) >= 18


def test_keyword_matching_uses_boundaries_and_negation() -> None:
    scorer = ComplexityScorer()

    false_positive_text = "This is a reasonable and notable vegetable example."
    negated_structured_text = "Do not return JSON, just plain text."

    assert scorer.score_text(false_positive_text).signals == ("simple_prompt",)
    assert "structured_output" not in scorer.score_text(negated_structured_text).signals


def test_instruction_markers_ignore_fenced_code_blocks() -> None:
    result = ComplexityScorer().score_text(
        """
        Inspect this snippet:
        ```python
        # - first internal note
        # - second internal note
        # - third internal note
        def ok():
            return True
        ```
        """
    )

    assert "code_detected" in result.signals
    assert "multi_step_instructions" not in result.signals


def test_system_message_scoring_is_explicitly_configurable() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "You must respond in JSON."},
            {"role": "user", "content": "Say hello."},
        ]
    }

    assert "You must respond in JSON" in extract_prompt_text(payload)
    assert "You must respond in JSON" not in extract_prompt_text(
        payload,
        include_system_messages=False,
    )
    assert "structured_output" in ComplexityScorer().score_payload(payload).signals
    assert "structured_output" not in ComplexityScorer(
        include_system_messages=False,
    ).score_payload(payload).signals


def test_estimator_uses_output_utilization_factor() -> None:
    estimator = CostEstimator(
        {"openai/test-model": ModelPricing(input_per_1m_usd=1.0, output_per_1m_usd=10.0)},
        default_output_tokens=512,
        output_token_utilization_factor=0.5,
    )

    estimate = estimator.estimate(
        provider="openai",
        model="test-model",
        prompt_text="hello",
        payload={"max_tokens": 100},
    )

    assert estimate.output_tokens == 50
    assert estimate.output_cost_usd == 0.0005


def test_estimator_accepts_precomputed_input_tokens() -> None:
    estimator = CostEstimator(
        {"openai/test-model": ModelPricing(input_per_1m_usd=1.0, output_per_1m_usd=10.0)},
        default_output_tokens=100,
    )

    estimate = estimator.estimate(
        provider="openai",
        model="test-model",
        prompt_text="hello",
        payload={},
        input_tokens=123,
    )

    assert estimate.input_tokens == 123
    assert estimate.input_cost_usd == 0.000123


def test_estimator_rejects_non_integer_token_budget() -> None:
    estimator = CostEstimator(
        {"openai/test-model": ModelPricing(input_per_1m_usd=1.0, output_per_1m_usd=10.0)},
        default_output_tokens=512,
    )

    with raises(InvalidTokenBudgetError):
        estimator.estimate(
            provider="openai",
            model="test-model",
            prompt_text="hello",
            payload={"max_tokens": 100.9},
        )


def test_estimator_warns_on_bare_model_pricing_fallback(caplog: LogCaptureFixture) -> None:
    estimator = CostEstimator(
        {"test-model": ModelPricing(input_per_1m_usd=1.0, output_per_1m_usd=10.0)},
        default_output_tokens=100,
    )

    estimate = estimator.estimate(
        provider="openai",
        model="test-model",
        prompt_text="hello",
        payload={},
    )

    assert estimate.output_tokens == 50
    assert "Pricing fallback" in caplog.text


def test_unknown_pricing_error_has_structured_fields() -> None:
    estimator = CostEstimator({}, default_output_tokens=100)

    with raises(UnknownPricingError) as exc_info:
        estimator.estimate(
            provider="openai",
            model="missing-model",
            prompt_text="hello",
            payload={},
        )

    assert exc_info.value.provider == "openai"
    assert exc_info.value.model == "missing-model"
    assert exc_info.value.pricing_key == "openai/missing-model"


def test_estimator_rejects_negative_pricing_values() -> None:
    bad_pricing = ModelPricing.model_construct(input_per_1m_usd=-1.0, output_per_1m_usd=1.0)

    with raises(ValueError, match="non-negative"):
        CostEstimator({"openai/bad-model": bad_pricing}, default_output_tokens=100)


def test_router_selects_endpoint_override() -> None:
    config = load_config("examples/costproof.yaml")
    decision, policy = RoutingEngine(config).route(
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "Summarize this."}]},
        RequestContext(
            organization=config.enterprise.organization,
            team=config.enterprise.team,
            project=config.project,
            endpoint="/api/analyze",
        ),
    )

    assert policy.allowed is True
    assert decision.selected_tier == "complex"
    assert decision.reason == "endpoint override forced tier complex"
    assert decision.selected_model == "gpt-4o"


def test_router_warns_without_audit_store(caplog: LogCaptureFixture) -> None:
    caplog.set_level("WARNING")

    RoutingEngine(load_config("examples/costproof.yaml"))

    assert "without an audit store" in caplog.text


def test_router_rejects_tier_without_models() -> None:
    config = load_config("examples/costproof.yaml")
    config.routing.tiers[0].models = []

    with raises(RoutingConfigurationError, match="simple"):
        RoutingEngine(config).route(
            {"messages": [{"role": "user", "content": "Hello"}]},
            RequestContext(
                organization=config.enterprise.organization,
                team=config.enterprise.team,
                project=config.project,
                endpoint="/v1/chat/completions",
            ),
        )


def test_downroute_reason_is_explicit_at_lowest_tier() -> None:
    config = CostProofConfig.model_validate(
        {
            "project": "lowest-tier",
            "budget": {
                "daily_limit_usd": 1.0,
                "monthly_limit_usd": 10.0,
                "warning_threshold": 0.8,
                "circuit_breaker_rate_usd_per_hour": 1.0,
            },
            "routing": {
                "fallback": "downroute",
                "tiers": [
                    {
                        "name": "simple",
                        "complexity_max": 1.0,
                        "models": [{"provider": "openai", "model": "gpt-4o-mini"}],
                        "cost_cap_per_request_usd": 0.00001,
                    }
                ],
            },
            "pricing": {
                "openai/gpt-4o-mini": {
                    "input_per_1m_usd": 0.15,
                    "output_per_1m_usd": 0.60,
                }
            },
        }
    )

    _decision, policy = RoutingEngine(config).route(
        {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 512},
        RequestContext(
            organization=config.enterprise.organization,
            team=config.enterprise.team,
            project=config.project,
            endpoint="/v1/chat/completions",
        ),
    )

    assert policy.allowed is False
    assert any("already at the lowest tier" in reason for reason in policy.reasons)


def test_router_reserves_budget_with_store(tmp_path: Path) -> None:
    config = CostProofConfig.model_validate(
        {
            "project": "reservation-test",
            "budget": {
                "daily_limit_usd": 0.000075,
                "monthly_limit_usd": 1.0,
                "warning_threshold": 0.8,
                "circuit_breaker_rate_usd_per_hour": 1.0,
            },
            "routing": {
                "fallback": "reject",
                "tiers": [
                    {
                        "name": "simple",
                        "complexity_max": 1.0,
                        "models": [{"provider": "openai", "model": "gpt-4o-mini"}],
                        "cost_cap_per_request_usd": 1.0,
                    }
                ],
            },
            "pricing": {
                "openai/gpt-4o-mini": {
                    "input_per_1m_usd": 0.0,
                    "output_per_1m_usd": 1.0,
                }
            },
        }
    )
    store = SQLiteAuditStore(tmp_path / "reservations.db")
    engine = RoutingEngine(config, store)
    context = RequestContext(
        organization=config.enterprise.organization,
        team=config.enterprise.team,
        project=config.project,
        endpoint="/v1/chat/completions",
    )
    payload = {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 100}

    first_decision, first_policy = engine.route(payload, context)
    second_decision, second_policy = engine.route(payload, context)

    assert first_policy.allowed is True
    assert second_policy.allowed is False
    assert second_policy.scopes[0].current_spend_usd == first_decision.estimated_cost_usd
    assert second_decision.estimated_cost_usd == first_decision.estimated_cost_usd


def test_budget_policy_blocks_before_provider_spend(tmp_path: Path) -> None:
    config = CostProofConfig.model_validate(
        {
            "project": "budget-test",
            "budget": {
                "daily_limit_usd": 0.00001,
                "monthly_limit_usd": 1.0,
                "warning_threshold": 0.8,
                "circuit_breaker_rate_usd_per_hour": 1.0,
            },
            "routing": {
                "fallback": "reject",
                "tiers": [
                    {
                        "name": "simple",
                        "complexity_max": 1.0,
                        "models": [{"provider": "openai", "model": "gpt-4o-mini"}],
                        "cost_cap_per_request_usd": 1.0,
                    }
                ],
            },
            "pricing": {
                "openai/gpt-4o-mini": {
                    "input_per_1m_usd": 0.15,
                    "output_per_1m_usd": 0.60,
                }
            },
        }
    )
    store = SQLiteAuditStore(tmp_path / "costproof.db")
    decision, policy = RoutingEngine(config, store).route(
        {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 512},
        RequestContext(
            organization=config.enterprise.organization,
            team=config.enterprise.team,
            project=config.project,
            endpoint="/v1/chat/completions",
        ),
    )

    assert decision.estimated_cost_usd > config.budget.daily_limit_usd
    assert policy.allowed is False
    assert policy.action == "block"
    assert any("budget would be exceeded" in reason for reason in policy.reasons)


def test_proxy_dry_run_records_audit_safe_decision(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COSTPROOF_CONFIG_PATH", str(Path("examples/costproof.yaml").resolve()))
    monkeypatch.setenv("COSTPROOF_SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("COSTPROOF_DRY_RUN", "true")
    proxy_app.get_settings.cache_clear()
    proxy_app.get_config.cache_clear()
    proxy_app.get_store.cache_clear()
    proxy_app.get_engine.cache_clear()

    client = TestClient(proxy_app.app)
    response = client.post(
        "/v1/chat/completions",
        headers={"x-costproof-endpoint": "/api/classify"},
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Classify this as support or sales."}],
            "max_tokens": 64,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o-mini"
    assert body["costproof"]["prompt_content_logged"] is False

    decisions = SQLiteAuditStore(tmp_path / "audit.db").recent_decisions()
    assert len(decisions) == 1
    assert decisions[0]["selected_model"] == "gpt-4o-mini"


def test_proxy_rejects_non_integer_token_budget(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COSTPROOF_CONFIG_PATH", str(Path("examples/costproof.yaml").resolve()))
    monkeypatch.setenv("COSTPROOF_SQLITE_PATH", str(tmp_path / "invalid.db"))
    monkeypatch.setenv("COSTPROOF_DRY_RUN", "true")
    proxy_app.get_settings.cache_clear()
    proxy_app.get_config.cache_clear()
    proxy_app.get_store.cache_clear()
    proxy_app.get_engine.cache_clear()

    client = TestClient(proxy_app.app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello."}],
            "max_tokens": 12.5,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["type"] == "costproof_invalid_token_budget"


def test_server_dashboard_renders_root(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COSTPROOF_CONFIG_PATH", str(Path("examples/costproof.yaml").resolve()))
    monkeypatch.setenv("COSTPROOF_SQLITE_PATH", str(tmp_path / "dashboard.db"))
    server_app.get_settings.cache_clear()
    server_app.get_config.cache_clear()
    server_app.get_store.cache_clear()

    client = TestClient(server_app.app)
    response = client.get("/")

    assert response.status_code == 200
    assert "CostProof" in response.text
    assert "LLM Spend Control" in response.text
    assert "Recent Routing Decisions" in response.text


def test_proxy_root_guides_browser_users(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COSTPROOF_CONFIG_PATH", str(Path("examples/costproof.yaml").resolve()))
    monkeypatch.setenv("COSTPROOF_SQLITE_PATH", str(tmp_path / "proxy.db"))
    monkeypatch.setenv("COSTPROOF_DRY_RUN", "true")
    proxy_app.get_settings.cache_clear()
    proxy_app.get_config.cache_clear()
    proxy_app.get_store.cache_clear()
    proxy_app.get_engine.cache_clear()

    client = TestClient(proxy_app.app)
    response = client.get("/")

    assert response.status_code == 200
    assert "CostProof Proxy" in response.text
    assert "/v1/chat/completions" in response.text
