"""CostProof command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from costproof.config import CostProofSettings, load_config
from costproof.config.settings import CostProofConfig, CostProofConfigError
from costproof.router.estimator import InvalidTokenBudgetError, UnknownPricingError
from costproof.router.engine import RoutingEngine
from costproof.router.models import BudgetEvaluation, RequestContext, RoutingDecision
from costproof.storage import SQLiteAuditStore


app = typer.Typer(help="CostProof local LLM cost governance.")
routes_app = typer.Typer(help="Validate and simulate routing decisions.")
budget_app = typer.Typer(help="Inspect local spend and budget state.")
app.add_typer(routes_app, name="routes")
app.add_typer(budget_app, name="budget")
console = Console()

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
    console.print(
        Panel.fit(
            f"[bold green]Valid configuration[/]\n"
            f"Project: [bold]{loaded.project}[/]\n"
            f"Tiers: {tier_count}  Providers: {provider_count}  Model routes: {model_count}",
            title="CostProof",
            border_style="green",
        )
    )


@routes_app.command("simulate")
def simulate_route(
    prompt: Annotated[str, typer.Argument(help="Prompt text to score and route.")],
    config: ConfigOption = Path("examples/costproof.yaml"),
    endpoint: Annotated[str, typer.Option("--endpoint", "-e")] = "/v1/chat/completions",
    max_tokens: Annotated[int, typer.Option("--max-tokens")] = 512,
    record: Annotated[
        bool,
        typer.Option("--record", help="Record the simulated decision in the local audit DB."),
    ] = False,
    sqlite: SQLiteOption = Path(CostProofSettings().sqlite_path),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print raw JSON instead of rich tables."),
    ] = False,
) -> None:
    """Simulate a route without calling a provider."""

    loaded = load_config(config)
    store = SQLiteAuditStore(sqlite) if record else None
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
    try:
        decision, policy = RoutingEngine(loaded, store).route(payload, context)
    except (InvalidTokenBudgetError, UnknownPricingError) as exc:
        console.print(f"[red]simulation failed:[/] {exc}")
        raise typer.Exit(1) from exc
    if record and store is not None:
        store.record_decision(decision, policy, "accepted" if policy.allowed else "blocked")

    payload = {
        "decision": decision.to_dict(),
        "policy": policy.to_dict(),
        "recorded": record,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    _print_simulation(decision, policy, recorded=record)


@budget_app.command("status")
def budget_status(
    config: ConfigOption = Path("examples/costproof.yaml"),
    sqlite: SQLiteOption = Path(CostProofSettings().sqlite_path),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print raw JSON instead of rich tables."),
    ] = False,
) -> None:
    """Show local spend summary from the audit database."""

    loaded = load_config(config)
    summary = SQLiteAuditStore(sqlite).spend_summary()
    payload = {
        "project": loaded.project,
        "organization": loaded.enterprise.organization,
        "budget": loaded.budget.model_dump(mode="json"),
        "spend": summary,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    _print_budget_status(loaded, summary)


def _print_simulation(
    decision: RoutingDecision,
    policy: BudgetEvaluation,
    *,
    recorded: bool,
) -> None:
    status = "[green]allowed[/]" if policy.allowed else "[red]blocked[/]"
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Policy", status)
    summary.add_row("Selected model", f"{decision.selected_provider}/{decision.selected_model}")
    summary.add_row("Tier", decision.selected_tier)
    summary.add_row("Complexity", f"{decision.complexity_score:.4f}")
    summary.add_row("Estimated cost", _money(decision.estimated_cost_usd))
    summary.add_row("Recorded", "yes" if recorded else "no")
    summary.add_row("Reason", decision.reason)
    console.print(Panel(summary, title="CostProof Route Simulation", border_style="blue"))

    scopes = Table(title="Budget Projection", show_lines=False)
    scopes.add_column("Scope")
    scopes.add_column("Name")
    scopes.add_column("Period")
    scopes.add_column("Current", justify="right")
    scopes.add_column("Projected", justify="right")
    scopes.add_column("Limit", justify="right")
    for scope in policy.scopes:
        scopes.add_row(
            scope.scope,
            scope.name,
            scope.period,
            _money(scope.current_spend_usd),
            _money(scope.projected_spend_usd),
            _money(scope.limit_usd),
        )
    console.print(scopes)


def _print_budget_status(config: CostProofConfig, summary: dict[str, object]) -> None:
    table = Table(title=f"CostProof Budget Status: {config.project}", show_lines=False)
    table.add_column("Period")
    table.add_column("Spend", justify="right")
    table.add_column("Limit", justify="right")
    table.add_column("Used", justify="right")
    table.add_row(
        "Today",
        _money(summary.get("today_usd", 0)),
        _money(config.budget.daily_limit_usd),
        _percent_text(summary.get("today_usd", 0), config.budget.daily_limit_usd),
    )
    table.add_row(
        "Month",
        _money(summary.get("month_usd", 0)),
        _money(config.budget.monthly_limit_usd),
        _percent_text(summary.get("month_usd", 0), config.budget.monthly_limit_usd),
    )
    table.add_row(
        "Last hour",
        _money(summary.get("last_hour_usd", 0)),
        _money(config.budget.circuit_breaker_rate_usd_per_hour),
        _percent_text(
            summary.get("last_hour_usd", 0),
            config.budget.circuit_breaker_rate_usd_per_hour,
        ),
    )
    console.print(table)

    breakdown = Table(title="Month-to-Date Breakdown", show_lines=False)
    breakdown.add_column("Dimension")
    breakdown.add_column("Name")
    breakdown.add_column("Spend", justify="right")
    for dimension in ("by_project", "by_team", "by_provider", "by_model"):
        values = summary.get(dimension)
        if isinstance(values, dict) and values:
            for name, spend in values.items():
                breakdown.add_row(dimension.removeprefix("by_"), str(name), _money(spend))
    if breakdown.row_count:
        console.print(breakdown)
    else:
        console.print("[dim]No recorded spend yet. Run a dry-run proxy request or simulate --record.[/]")


def _money(value: object) -> str:
    amount = _float(value)
    if amount == 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.6f}"
    return f"${amount:,.2f}"


def _percent_text(value: object, limit: float) -> str:
    if limit <= 0:
        return "n/a"
    return f"{_float(value) / limit * 100:.2f}%"


def _float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


if __name__ == "__main__":
    app()
