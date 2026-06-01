"""FastAPI application for the CostProof dashboard and management API."""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from costproof.config import CostProofConfig, CostProofSettings, load_config
from costproof.storage import SQLiteAuditStore


app = FastAPI(
    title="CostProof Server",
    description="Local dashboard and enterprise spend-management API.",
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


@app.get("/health")
async def health() -> dict[str, object]:
    """Basic health endpoint for deployment checks."""

    config = get_config()
    return {
        "status": "ok",
        "project": config.project,
        "organization": config.enterprise.organization,
    }


@app.get("/api/config")
async def config_view() -> dict[str, object]:
    """Return effective config without secret values."""

    return get_config().model_dump(mode="json")


@app.get("/api/spend/summary")
async def spend_summary() -> dict[str, object]:
    """Return local spend summary from audit records."""

    return get_store().spend_summary()


@app.get("/api/routing/decisions")
async def routing_decisions(limit: int = 100) -> dict[str, object]:
    """Return recent audit-safe routing decisions."""

    return {"decisions": get_store().recent_decisions(limit=limit)}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    """Expose a minimal Prometheus text endpoint for enterprise observability."""

    summary = get_store().spend_summary()
    lines = [
        "# HELP costproof_spend_usd Estimated CostProof spend in USD.",
        "# TYPE costproof_spend_usd gauge",
        f'costproof_spend_usd{{period="today"}} {summary["today_usd"]}',
        f'costproof_spend_usd{{period="month"}} {summary["month_usd"]}',
        f'costproof_spend_usd{{period="last_hour"}} {summary["last_hour_usd"]}',
    ]
    by_project = summary.get("by_project")
    if isinstance(by_project, dict):
        for project, spend in by_project.items():
            lines.append(f'costproof_project_spend_usd{{project="{_label(project)}"}} {spend}')
    by_model = summary.get("by_model")
    if isinstance(by_model, dict):
        for model, spend in by_model.items():
            lines.append(f'costproof_model_spend_usd{{model="{_label(model)}"}} {spend}')
    return "\n".join(lines) + "\n"


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')
