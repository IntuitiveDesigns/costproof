"""FastAPI application for the OpenAI-compatible CostProof proxy."""

from __future__ import annotations

import time
from functools import lru_cache
from html import escape
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from costproof.adapters import OpenAICompatibleAdapter, ProviderAdapterError
from costproof.config import CostProofConfig, CostProofSettings, load_config
from costproof.router.engine import RoutingEngine
from costproof.router.models import BudgetEvaluation, RequestContext, RoutingDecision
from costproof.storage import SQLiteAuditStore


app = FastAPI(
    title="CostProof Proxy",
    description="OpenAI-compatible LLM cost-governance proxy.",
    version="0.1.0",
)


@lru_cache
def get_settings() -> CostProofSettings:
    """Return cached process settings."""

    return CostProofSettings()


@lru_cache
def get_config() -> CostProofConfig:
    """Return cached YAML configuration."""

    return load_config(get_settings().config_path)


@lru_cache
def get_store() -> SQLiteAuditStore:
    """Return cached local audit store."""

    return SQLiteAuditStore(get_settings().sqlite_path)


@lru_cache
def get_engine() -> RoutingEngine:
    """Return cached routing engine."""

    return RoutingEngine(get_config(), get_store())


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    """Render a small proxy status page for browser visits."""

    config = get_config()
    dry_run = "on" if _effective_dry_run(None) else "off"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CostProof Proxy</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f6f8fb;
      color: #152033;
      font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(720px, calc(100vw - 32px));
      background: white;
      border: 1px solid #d7dee8;
      border-radius: 8px;
      box-shadow: 0 10px 28px rgba(21, 32, 51, 0.08);
      padding: 28px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    p {{ color: #667085; margin: 0 0 18px; }}
    code {{
      display: block;
      background: #111827;
      color: #e5e7eb;
      border-radius: 6px;
      padding: 10px 12px;
      overflow-x: auto;
    }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }}
    .badge {{
      border: 1px solid #d7dee8;
      border-radius: 999px;
      padding: 6px 10px;
      color: #667085;
    }}
    a {{ color: #275fbc; }}
  </style>
</head>
<body>
  <main>
    <h1>CostProof Proxy</h1>
    <p>This service accepts OpenAI-compatible chat-completions requests.</p>
    <div class="meta">
      <span class="badge">Project: {escape(config.project)}</span>
      <span class="badge">Organization: {escape(config.enterprise.organization)}</span>
      <span class="badge">Dry run: {dry_run}</span>
    </div>
    <code>POST http://127.0.0.1:4000/v1/chat/completions</code>
    <p style="margin-top:18px;">Open the dashboard at <a href="http://127.0.0.1:4001/">http://127.0.0.1:4001/</a>.</p>
  </main>
</body>
</html>"""


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Return an empty favicon response to avoid noisy browser 404s."""

    return Response(status_code=204)


@app.get("/health")
async def health() -> dict[str, object]:
    """Health endpoint with enough context for deployment checks."""

    config = get_config()
    return {
        "status": "ok",
        "project": config.project,
        "organization": config.enterprise.organization,
        "dry_run": _effective_dry_run(None),
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    payload: dict[str, Any],
    x_costproof_project: str | None = Header(default=None),
    x_costproof_team: str | None = Header(default=None),
    x_costproof_org: str | None = Header(default=None),
    x_costproof_endpoint: str | None = Header(default=None),
    x_costproof_dry_run: str | None = Header(default=None),
) -> dict[str, Any]:
    """Route and optionally forward an OpenAI-compatible chat request."""

    config = get_config()
    context = _request_context(
        config=config,
        request=request,
        project=x_costproof_project,
        team=x_costproof_team,
        organization=x_costproof_org,
        endpoint=x_costproof_endpoint,
    )
    decision, policy = get_engine().route(payload, context)

    if not policy.allowed:
        get_store().record_decision(decision, policy, "blocked")
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "type": "costproof_budget_blocked",
                    "message": "CostProof blocked the request before provider spend occurred.",
                    "reasons": list(policy.reasons),
                },
                "costproof": _costproof_payload(decision, policy),
            },
        )

    dry_run = _effective_dry_run(x_costproof_dry_run)
    if dry_run:
        get_store().record_decision(decision, policy, "accepted")
        return _dry_run_response(decision, policy)

    if payload.get("stream") is True and config.proxy.reject_streaming_when_forwarding:
        get_store().record_decision(decision, policy, "failed")
        raise HTTPException(
            status_code=501,
            detail={
                "error": {
                    "type": "costproof_streaming_not_implemented",
                    "message": "Streaming forwarding is not implemented in this local proxy build.",
                },
                "costproof": _costproof_payload(decision, policy),
            },
        )

    provider_config = config.providers.get(decision.selected_provider)
    if provider_config is None or not provider_config.enabled:
        get_store().record_decision(decision, policy, "failed")
        raise HTTPException(
            status_code=502,
            detail={
                "error": {
                    "type": "costproof_provider_unavailable",
                    "message": f"Provider {decision.selected_provider!r} is not enabled.",
                },
                "costproof": _costproof_payload(decision, policy),
            },
        )

    if provider_config.type != "openai_compatible":
        get_store().record_decision(decision, policy, "failed")
        raise HTTPException(
            status_code=501,
            detail={
                "error": {
                    "type": "costproof_adapter_not_implemented",
                    "message": f"Provider type {provider_config.type!r} is not implemented yet.",
                },
                "costproof": _costproof_payload(decision, policy),
            },
        )

    forwarded_payload = dict(payload)
    forwarded_payload["model"] = decision.selected_model
    try:
        response = await OpenAICompatibleAdapter().chat_completions(
            forwarded_payload,
            provider_config,
        )
    except ProviderAdapterError as exc:
        get_store().record_decision(decision, policy, "failed")
        raise HTTPException(
            status_code=502,
            detail={
                "error": {"type": "costproof_provider_error", "message": str(exc)},
                "costproof": _costproof_payload(decision, policy),
            },
        ) from exc

    response["costproof"] = _costproof_payload(decision, policy)
    get_store().record_decision(decision, policy, "completed")
    return response


def _request_context(
    *,
    config: CostProofConfig,
    request: Request,
    project: str | None,
    team: str | None,
    organization: str | None,
    endpoint: str | None,
) -> RequestContext:
    enterprise = config.enterprise
    if enterprise.require_project_header and not project:
        raise HTTPException(status_code=400, detail="x-costproof-project header is required")
    if enterprise.require_team_header and not team:
        raise HTTPException(status_code=400, detail="x-costproof-team header is required")
    if enterprise.require_organization_header and not organization:
        raise HTTPException(status_code=400, detail="x-costproof-org header is required")

    return RequestContext(
        organization=organization or enterprise.organization,
        team=team or enterprise.team,
        project=project or config.project,
        endpoint=endpoint or request.url.path,
    )


def _effective_dry_run(header_value: str | None) -> bool:
    settings = get_settings()
    config_dry_run = get_config().proxy.dry_run
    default = settings.dry_run if config_dry_run is None else config_dry_run
    if header_value is None:
        return default
    return header_value.strip().lower() in {"1", "true", "yes", "on"}


def _dry_run_response(decision: RoutingDecision, policy: BudgetEvaluation) -> dict[str, Any]:
    return {
        "id": decision.request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": decision.selected_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": decision.estimated_input_tokens,
            "completion_tokens": decision.estimated_output_tokens,
            "total_tokens": decision.estimated_input_tokens + decision.estimated_output_tokens,
        },
        "costproof": _costproof_payload(decision, policy),
    }


def _costproof_payload(
    decision: RoutingDecision,
    policy: BudgetEvaluation,
) -> dict[str, object]:
    return {
        "decision": decision.to_dict(),
        "policy": policy.to_dict(),
        "prompt_content_logged": False,
    }
