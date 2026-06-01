"""Enterprise routing and budget behavior tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import costproof.proxy.app as proxy_app
from costproof import __version__
from costproof.config import load_config
from costproof.config.settings import CostProofConfig
from costproof.router.engine import RoutingEngine
from costproof.router.models import RequestContext
from costproof.router.scoring import ComplexityScorer
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
