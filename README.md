# CostProof

CostProof is a local-first LLM cost governor and model arbitrage proxy. It sits between
applications and LLM providers, exposes an OpenAI-compatible interface, routes requests to
the cheapest capable configured model, enforces spend limits, and records audit-safe cost
attribution.

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
