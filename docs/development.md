# Development Notes

## Local Checks

```bash
python -m pytest
python -m ruff check .
python -m mypy src
```

## Proxy Server

```bash
python -m uvicorn costproof.proxy.app:app --reload --port 4000
```

Useful headers:

- `x-costproof-project`: override configured project attribution.
- `x-costproof-team`: override configured team attribution.
- `x-costproof-org`: override configured organization attribution.
- `x-costproof-endpoint`: match application endpoint overrides such as `/api/classify`.
- `x-costproof-dry-run`: force dry-run behavior for a request.

## Management API

```bash
python -m uvicorn costproof.server.app:app --reload --port 4001
```

Implemented endpoints:

- `GET /health`
- `GET /api/config`
- `GET /api/spend/summary`
- `GET /api/routing/decisions?limit=100`
- `GET /metrics`

## CLI

```bash
python -m costproof.cli routes validate --config examples/costproof.yaml
python -m costproof.cli routes simulate "Classify this ticket" --endpoint /api/classify
python -m costproof.cli routes simulate "Classify this ticket" --endpoint /api/classify --record
python -m costproof.cli budget status --config examples/costproof.yaml
```

## Live Forwarding

The example config defaults to dry-run mode. To forward to an OpenAI-compatible provider,
set a provider API key environment variable and disable dry-run:

```bash
export OPENAI_API_KEY=...
export COSTPROOF_DRY_RUN=false
```

Streaming forwarding is intentionally rejected in this slice until the proxy has a tested
streaming path.
