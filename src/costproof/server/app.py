"""FastAPI application for the CostProof dashboard and management API."""

from __future__ import annotations

from functools import lru_cache
from html import escape

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

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


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Render the local CostProof dashboard."""

    config = get_config()
    store = get_store()
    summary = store.spend_summary()
    decisions = store.recent_decisions(limit=50)
    return _render_dashboard(config, summary, decisions)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Return an empty favicon response to avoid noisy browser 404s."""

    return Response(status_code=204)


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
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_dashboard(
    config: CostProofConfig,
    summary: dict[str, object],
    decisions: list[dict[str, object]],
) -> str:
    today_spend = _money(summary.get("today_usd", 0))
    month_spend = _money(summary.get("month_usd", 0))
    hour_spend = _money(summary.get("last_hour_usd", 0))
    daily_pct = _percent(_float(summary.get("today_usd", 0)), config.budget.daily_limit_usd)
    monthly_pct = _percent(_float(summary.get("month_usd", 0)), config.budget.monthly_limit_usd)
    hour_pct = _percent(
        _float(summary.get("last_hour_usd", 0)),
        config.budget.circuit_breaker_rate_usd_per_hour,
    )
    provider_rows = _breakdown_rows("Provider", summary.get("by_provider"))
    model_rows = _breakdown_rows("Model", summary.get("by_model"))
    decision_rows = _decision_rows(decisions)
    tier_rows = _tier_rows(config)
    dry_run = config.proxy.dry_run

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CostProof Dashboard</title>
  <style>
    :root {{
      --bg: #f6f8fb;
      --surface: #ffffff;
      --surface-muted: #eef3f8;
      --ink: #152033;
      --muted: #667085;
      --line: #d7dee8;
      --green: #16815d;
      --amber: #b7791f;
      --red: #c24141;
      --blue: #275fbc;
      --teal: #0f766e;
      --shadow: 0 10px 28px rgba(21, 32, 51, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    a {{ color: inherit; }}
    .shell {{
      min-height: 100vh;
      display: grid;
      grid-template-columns: 252px 1fr;
    }}
    aside {{
      background: #111827;
      color: #e5e7eb;
      padding: 24px 20px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 750;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .mark {{
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      border-radius: 7px;
      background: #f9fafb;
      color: #111827;
      font-weight: 900;
    }}
    .nav {{
      display: grid;
      gap: 8px;
    }}
    .nav a {{
      text-decoration: none;
      color: #cbd5e1;
      padding: 9px 10px;
      border-radius: 6px;
    }}
    .nav a.active, .nav a:hover {{
      background: rgba(255,255,255,0.08);
      color: #ffffff;
    }}
    .side-meta {{
      margin-top: auto;
      color: #9ca3af;
      display: grid;
      gap: 8px;
      font-size: 12px;
    }}
    main {{
      padding: 28px 32px 40px;
      min-width: 0;
    }}
    .topbar {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .subtitle {{
      color: var(--muted);
      margin: 0;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }}
    .badge {{
      border: 1px solid var(--line);
      background: var(--surface);
      border-radius: 999px;
      padding: 6px 10px;
      color: var(--muted);
      white-space: nowrap;
    }}
    .badge.ok {{
      color: var(--green);
      border-color: #b8decf;
      background: #edf8f3;
    }}
    .grid {{
      display: grid;
      gap: 18px;
    }}
    .metrics {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 18px;
      min-width: 0;
    }}
    .metric-label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }}
    .metric-value {{
      font-size: 28px;
      line-height: 1.1;
      font-weight: 780;
      letter-spacing: 0;
      margin-bottom: 12px;
    }}
    .meter {{
      height: 9px;
      border-radius: 999px;
      background: var(--surface-muted);
      overflow: hidden;
    }}
    .meter > span {{
      display: block;
      height: 100%;
      width: var(--value);
      background: var(--bar, var(--blue));
      border-radius: inherit;
    }}
    .metric-foot {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .two-col {{
      grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
      margin-top: 18px;
    }}
    .section-title {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 12px;
    }}
    h2 {{
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }}
    td {{
      font-size: 13px;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .mono {{
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }}
    .status {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 8px;
      background: #edf8f3;
      color: var(--green);
      font-size: 12px;
      font-weight: 650;
    }}
    .status.blocked {{
      background: #fff1f2;
      color: var(--red);
    }}
    .list {{
      display: grid;
      gap: 12px;
    }}
    .breakdown {{
      display: grid;
      gap: 6px;
    }}
    .breakdown-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
    }}
    .muted {{
      color: var(--muted);
    }}
    .empty {{
      color: var(--muted);
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 16px;
      background: #fbfcfe;
    }}
    .tiers {{
      display: grid;
      gap: 10px;
    }}
    .tier {{
      display: grid;
      gap: 5px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fbfcfe;
    }}
    .tier-head {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-weight: 700;
    }}
    .commands {{
      display: grid;
      gap: 8px;
      margin-top: 18px;
    }}
    code {{
      display: block;
      padding: 10px 12px;
      border-radius: 6px;
      background: #111827;
      color: #e5e7eb;
      overflow-x: auto;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }}
    @media (max-width: 980px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ position: static; }}
      .metrics, .two-col {{ grid-template-columns: 1fr; }}
      .topbar {{ flex-direction: column; }}
      .badges {{ justify-content: flex-start; }}
      main {{ padding: 22px 16px 32px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand"><span class="mark">CP</span><span>CostProof</span></div>
      <nav class="nav">
        <a class="active" href="/">Dashboard</a>
        <a href="/api/spend/summary">Spend API</a>
        <a href="/api/routing/decisions">Decisions API</a>
        <a href="/metrics">Metrics</a>
        <a href="/docs">OpenAPI</a>
      </nav>
      <div class="side-meta">
        <div>Project: <strong>{escape(config.project)}</strong></div>
        <div>Team: <strong>{escape(config.enterprise.team)}</strong></div>
        <div>Org: <strong>{escape(config.enterprise.organization)}</strong></div>
      </div>
    </aside>
    <main>
      <div class="topbar">
        <div>
          <h1>LLM Spend Control</h1>
          <p class="subtitle">Local routing, budget policy, and audit-safe cost attribution.</p>
        </div>
        <div class="badges">
          <span class="badge ok">Server online</span>
          <span class="badge">{'Dry run on' if dry_run else 'Forwarding on'}</span>
          <span class="badge">No prompt logging</span>
        </div>
      </div>

      <section class="grid metrics">
        {_metric_panel('Today', today_spend, config.budget.daily_limit_usd, daily_pct, '--green')}
        {_metric_panel('Month', month_spend, config.budget.monthly_limit_usd, monthly_pct, '--blue')}
        {_metric_panel('Last hour', hour_spend, config.budget.circuit_breaker_rate_usd_per_hour, hour_pct, '--amber')}
      </section>

      <section class="grid two-col">
        <div class="panel">
          <div class="section-title">
            <h2>Recent Routing Decisions</h2>
            <span class="muted">{len(decisions)} shown</span>
          </div>
          {decision_rows}
        </div>
        <div class="grid">
          <div class="panel">
            <div class="section-title"><h2>Spend Breakdown</h2><span class="muted">Month to date</span></div>
            <div class="list">{provider_rows}{model_rows}</div>
          </div>
          <div class="panel">
            <div class="section-title"><h2>Routing Ladder</h2><span class="muted">{len(config.routing.tiers)} tiers</span></div>
            <div class="tiers">{tier_rows}</div>
          </div>
        </div>
      </section>

      <section class="panel commands">
        <div class="section-title"><h2>Demo Commands</h2><span class="muted">Populate the dashboard without provider spend</span></div>
        <code>python -m costproof.cli routes simulate "Classify this support ticket" --endpoint /api/classify --record</code>
        <code>python -m uvicorn costproof.proxy.app:app --reload --port 4000</code>
      </section>
    </main>
  </div>
</body>
</html>"""


def _metric_panel(label: str, value: str, limit: float, pct: float, color_var: str) -> str:
    return f"""
        <div class="panel">
          <div class="metric-label">{escape(label)}</div>
          <div class="metric-value">{value}</div>
          <div class="meter" aria-label="{escape(label)} budget usage">
            <span style="--value: {min(pct, 100):.2f}%; --bar: var({color_var});"></span>
          </div>
          <div class="metric-foot">{pct:.2f}% of {_money(limit)} limit</div>
        </div>
    """


def _breakdown_rows(label: str, value: object) -> str:
    if not isinstance(value, dict) or not value:
        return f'<div class="empty">No {escape(label.lower())} spend recorded yet.</div>'

    rows = []
    max_value = max((_float(spend) for spend in value.values()), default=0.0)
    for name, spend in value.items():
        width = _percent(_float(spend), max_value)
        rows.append(
            f"""
            <div class="breakdown">
              <div class="breakdown-row">
                <span>{escape(str(name))}</span>
                <strong>{_money(spend)}</strong>
              </div>
              <div class="meter"><span style="--value: {width:.2f}%; --bar: var(--teal);"></span></div>
            </div>
            """
        )
    return "".join(rows)


def _decision_rows(decisions: list[dict[str, object]]) -> str:
    if not decisions:
        return (
            '<div class="empty">No decisions recorded yet. Send a dry-run proxy request or run '
            '<span class="mono">routes simulate --record</span>.</div>'
        )

    rows = []
    for decision in decisions:
        status = str(decision.get("status", "unknown"))
        status_class = " blocked" if status == "blocked" else ""
        rows.append(
            "<tr>"
            f"<td class=\"mono\">{escape(str(decision.get('timestamp', '')))}</td>"
            f"<td>{escape(str(decision.get('project', '')))}</td>"
            f"<td>{escape(str(decision.get('selected_tier', '')))}</td>"
            f"<td>{escape(str(decision.get('selected_model', '')))}</td>"
            f"<td>{_money(decision.get('estimated_cost_usd', 0))}</td>"
            f"<td><span class=\"status{status_class}\">{escape(status)}</span></td>"
            "</tr>"
        )
    return (
        "<table>"
        "<thead><tr><th>Time</th><th>Project</th><th>Tier</th><th>Model</th><th>Est. cost</th>"
        "<th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _tier_rows(config: CostProofConfig) -> str:
    rows = []
    for tier in config.routing.tiers:
        models = ", ".join(f"{model.provider}/{model.model}" for model in tier.models)
        rows.append(
            f"""
            <div class="tier">
              <div class="tier-head">
                <span>{escape(tier.name)}</span>
                <span>{tier.complexity_max:.2f}</span>
              </div>
              <div class="muted">{escape(models)}</div>
              <div class="muted">Cap: {_money(tier.cost_cap_per_request_usd)}</div>
            </div>
            """
        )
    return "".join(rows)


def _float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _money(value: object) -> str:
    amount = _float(value)
    if amount == 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.6f}"
    return f"${amount:,.2f}"


def _percent(value: float, limit: float) -> float:
    if limit <= 0:
        return 0.0
    return max(0.0, value / limit * 100)
