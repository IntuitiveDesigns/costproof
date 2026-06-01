"""CostProof command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from costproof.config import CostProofSettings, load_config
from costproof.config.settings import CostProofConfigError
from costproof.router.engine import RoutingEngine
from costproof.router.models import RequestContext
from costproof.storage import SQLiteAuditStore


app = typer.Typer(help="CostProof local LLM cost governance.")
routes_app = typer.Typer(help="Validate and simulate routing decisions.")
budget_app = typer.Typer(help="Inspect local spend and budget state.")
app.add_typer(routes_app, name="routes")
app.add_typer(budget_app, name="budget")

ConfigOption = Annotated[
    Path,
    typer.Option("--config", "-c", help="Path to costproof.yaml."),
]
SQLiteOption = Annotated[
    Path,
    typer.Option("--sqlite", help="Path to the local CostProof SQLite audit database."),
]


@app.callback()
def main() -> None:
    """CostProof command-line interface."""


@app.command()
def version() -> None:
    """Print the installed CostProof version."""

    from costproof import __version__

    typer.echo(__version__)


@routes_app.command("validate")
def validate_routes(config: ConfigOption = Path("examples/costproof.yaml")) -> None:
    """Validate routing, provider, and pricing configuration."""

    try:
        loaded = load_config(config)
    except CostProofConfigError as exc:
        typer.echo(f"invalid: {exc}", err=True)
        raise typer.Exit(1) from exc

    tier_count = len(loaded.routing.tiers)
    provider_count = len(loaded.providers)
    model_count = sum(len(tier.models) for tier in loaded.routing.tiers)
    typer.echo(
        f"valid: project={loaded.project} tiers={tier_count} "
        f"providers={provider_count} model_routes={model_count}"
    )


@routes_app.command("simulate")
def simulate_route(
    prompt: Annotated[str, typer.Argument(help="Prompt text to score and route.")],
    config: ConfigOption = Path("examples/costproof.yaml"),
    endpoint: Annotated[str, typer.Option("--endpoint", "-e")] = "/v1/chat/completions",
    max_tokens: Annotated[int, typer.Option("--max-tokens")] = 512,
) -> None:
    """Simulate a route without recording spend or calling a provider."""

    loaded = load_config(config)
    payload = {
        "model": "requested-by-app",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    context = RequestContext(
        organization=loaded.enterprise.organization,
        team=loaded.enterprise.team,
        project=loaded.project,
        endpoint=endpoint,
    )
    decision, policy = RoutingEngine(loaded).route(payload, context)
    typer.echo(
        json.dumps(
            {"decision": decision.to_dict(), "policy": policy.to_dict()},
            indent=2,
            sort_keys=True,
        )
    )


@budget_app.command("status")
def budget_status(
    config: ConfigOption = Path("examples/costproof.yaml"),
    sqlite: SQLiteOption = Path(CostProofSettings().sqlite_path),
) -> None:
    """Show local spend summary from the audit database."""

    loaded = load_config(config)
    summary = SQLiteAuditStore(sqlite).spend_summary()
    typer.echo(
        json.dumps(
            {
                "project": loaded.project,
                "organization": loaded.enterprise.organization,
                "budget": loaded.budget.model_dump(mode="json"),
                "spend": summary,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    app()
