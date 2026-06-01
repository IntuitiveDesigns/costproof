# CostProof Architecture Notes

CostProof is organized around a local governance loop:

1. The proxy receives an OpenAI-compatible chat request.
2. Request context is resolved from CostProof headers and YAML defaults.
3. The router scores prompt complexity without external calls.
4. The selected tier/model is cost-estimated from local pricing.
5. Per-request caps, project budgets, team/org budgets, and circuit breakers are evaluated.
6. The request is blocked, dry-run returned, or forwarded through a provider adapter.
7. An audit-safe decision record is written to SQLite without prompt content.

## Implemented Components

- `costproof-proxy`: `POST /v1/chat/completions`, health checks, dry-run response shape,
  budget blocking, and OpenAI-compatible forwarding.
- `costproof-router`: rule-based complexity scorer, endpoint overrides, tier selection,
  per-request cost cap handling, and enterprise budget policy evaluation.
- `costproof-storage`: SQLite audit log and spend aggregation.
- `costproof-server`: local API for config, spend summaries, routing decisions, and
  Prometheus-style metrics.
- `costproof-cli`: route validation, route simulation, and local budget status.
- `costproof-sdk`: Python and TypeScript clients for the management API.

## Enterprise Controls

Enterprise adoption is represented in YAML rather than cloud dependencies:

- Organization, team, and project attribution.
- Optional required tenancy headers.
- Team and organization daily budgets.
- Project daily/monthly budgets.
- Hourly organization circuit breaker.
- Explicit prompt logging control, defaulting to disabled.
- Local SQLite audit records for decision provenance.

## Deferred Work

- Native Anthropic/Gemini/Bedrock/Azure adapters.
- Streaming response forwarding.
- Actual post-response cost reconciliation from provider usage metadata.
- Encrypted key store and BYO KMS integrations.
- Full dashboard UI.
