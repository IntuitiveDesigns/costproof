# CostProof

[![Stars](https://img.shields.io/github/stars/IntuitiveDesigns/costproof?style=social)](https://github.com/IntuitiveDesigns/costproof/stargazers)
[![Watchers](https://img.shields.io/github/watchers/IntuitiveDesigns/costproof?style=social)](https://github.com/IntuitiveDesigns/costproof/watchers)
[![PyPI downloads](https://img.shields.io/pypi/dm/costproof?label=PyPI%20downloads)](https://pypi.org/project/costproof/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLite](https://img.shields.io/badge/SQLite-local%20audit-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-SDK-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![StreamKernel](https://img.shields.io/badge/StreamKernel-Proof%20Suite-1f6feb)](https://streamkernel.io/)
[![License](https://img.shields.io/badge/license-Apache--2.0%20%2F%20BSL--1.1-blue)](LICENSE)

CostProof is a local-first LLM cost governor and model arbitrage proxy. It sits between
applications and LLM providers, exposes an OpenAI-compatible interface, routes requests to
the cheapest capable configured model, enforces spend limits, and records audit-safe cost
attribution.

CostProof is part of the Proof Suite by StreamKernel LLC, built for teams that want local
control over production AI infrastructure without turning cost governance into another
cloud dependency.

## Current Implementation

This repo now includes an enterprise-adoption foundation:

- OpenAI-compatible `POST /v1/chat/completions` proxy route
- Validated YAML config for routing tiers, providers, pricing, budgets, and tenancy defaults
- Rule-based complexity scorer with explainable signals and no external calls
- Per-request cost estimates from a local pricing table
- Hard project daily/monthly budgets, team/org daily budgets, and hourly circuit breaker
- SQLite request audit log that does not persist prompt content
- Dashboard/API endpoints for config, spend summaries, recent routing decisions, and metrics
- CLI commands for route validation, route simulation, and budget status
- Python and TypeScript SDK clients for local management API queries

The proxy defaults to dry-run mode in `examples/costproof.yaml` so teams can test routing
and policy behavior before forwarding live provider traffic.

## Repository Layout

```text
costproof/
  src/costproof/
    proxy/          OpenAI-compatible proxy route and provider forwarding
    router/         Complexity scoring, tier selection, cost estimation, budget policy
    adapters/       Provider adapter interfaces and OpenAI-compatible adapter
    server/         Local dashboard/API and Prometheus-style metrics endpoint
    cli/            Configuration, routing, and budget commands
    sdk/python/     Python management API client
    config/         YAML schemas and runtime settings
    storage/        SQLite audit and spend store
    security/       Local key storage placeholder
  packages/
    typescript-sdk/ TypeScript management API client
  tests/            Python routing, policy, and proxy tests
  docs/             Architecture and development notes
  examples/         Example enterprise-ready routing configuration
```

## Development Setup

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run Locally

Validate the example config:

```bash
costproof routes validate --config examples/costproof.yaml
```

Simulate a route without calling a provider:

```bash
costproof routes simulate "Classify this support ticket" --endpoint /api/classify
```

Run the proxy:

```bash
uvicorn costproof.proxy.app:app --reload --port 4000
```

Run the management API:

```bash
uvicorn costproof.server.app:app --reload --port 4001
```

Query budget status:

```bash
costproof budget status --config examples/costproof.yaml
```

## Design Principles

- Local-first by default
- No prompt content logged by default
- Provider-agnostic routing decisions
- OpenAI-compatible proxy interface
- Transparent routing decision provenance
- Small, reviewable implementation phases

## Verification

```bash
python -m pytest
python -m ruff check .
python -m mypy src
```

## License

CostProof uses the same component-level licensing model as CostProof across the
StreamKernel Proof Suite.

- `costproof-proxy` (Python): [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
  for the core proxy, routing engine, OpenAI/Anthropic/Ollama adapters, and CLI.
- `costproof-server`: [Business Source License 1.1](https://mariadb.com/bsl11/) for
  the dashboard, team API, per-project attribution, alerting, and policy engine.
- `costproof-sdk` (Python/TypeScript): [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)
  for programmatic spend querying.

See [LICENSE](LICENSE) for component paths, BSL parameters, and allowed free local
dashboard use.

## Pricing Tiers

- Free (Open Source Core): Proxy, routing engine, OpenAI/Anthropic/Ollama adapters, CLI,
  local dashboard, unlimited requests. No credit card required.
- Individual: $19/month. Adds cloud dashboard sync, 90-day history, email alerts,
  circuit breaker notifications, and priority support.
- Team: $179/month, up to 10 seats. Adds per-project attribution, team spend policies,
  Slack alerts, GitHub Actions cost gate, and SSO.
- Enterprise: Custom. Adds BYO key management, air-gapped deployment, SLA,
  Datadog/Prometheus export, and dedicated onboarding.

---

*A product of [StreamKernel LLC](https://streamkernel.io/) · [costproof.io](https://costproof.io/) · [steven.lopez@streamkernel.io](mailto:steven.lopez@streamkernel.io)*
