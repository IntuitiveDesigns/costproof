"""SQLite-backed audit and spend storage."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from costproof.router.models import BudgetEvaluation, DecisionStatus, RequestContext, RoutingDecision


class SQLiteAuditStore:
    """Local-first audit store that never persists prompt content."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._ensure_schema()

    def record_decision(
        self,
        decision: RoutingDecision,
        policy: BudgetEvaluation,
        status: DecisionStatus,
        *,
        actual_cost_usd: float | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Persist one routing decision and its policy outcome."""

        params = (
            decision.request_id,
            decision.timestamp.isoformat(),
            decision.organization,
            decision.team,
            decision.project,
            decision.endpoint,
            decision.selected_tier,
            decision.selected_provider,
            decision.selected_model,
            decision.requested_model,
            decision.complexity_score,
            json.dumps(list(decision.complexity_signals)),
            decision.estimated_input_tokens,
            decision.estimated_output_tokens,
            decision.estimated_cost_usd,
            actual_cost_usd,
            decision.cost_cap_usd,
            int(decision.fallback_applied),
            decision.reason,
            json.dumps(list(decision.warnings) + list(policy.warnings)),
            policy.action,
            json.dumps(list(policy.reasons)),
            status,
        )
        if connection is not None:
            self._record_decision(connection, params)
            return

        with self._connect() as conn:
            self._record_decision(conn, params)

    def spend_since(
        self,
        *,
        context: RequestContext,
        scope: str,
        since: datetime,
        connection: sqlite3.Connection | None = None,
    ) -> float:
        """Return accepted/completed estimated spend since a point in time."""

        where = ["timestamp >= ?", "status IN ('accepted', 'completed')"]
        params: list[object] = [since.isoformat()]
        if scope == "project":
            where.extend(["organization = ?", "team = ?", "project = ?"])
            params.extend([context.organization, context.team, context.project])
        elif scope == "team":
            where.extend(["organization = ?", "team = ?"])
            params.extend([context.organization, context.team])
        elif scope == "organization":
            where.append("organization = ?")
            params.append(context.organization)
        else:
            raise ValueError(f"unknown budget scope: {scope}")

        if connection is not None:
            return self._spend_since(connection, where, params)

        with self._connect() as conn:
            return self._spend_since(conn, where, params)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a write transaction for atomic budget check-and-record operations."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def _record_decision(
        self,
        conn: sqlite3.Connection,
        params: tuple[object, ...],
    ) -> None:
        conn.execute(
            """
            INSERT INTO request_audit (
                request_id, timestamp, organization, team, project, endpoint,
                selected_tier, selected_provider, selected_model, requested_model,
                complexity_score, complexity_signals, estimated_input_tokens,
                estimated_output_tokens, estimated_cost_usd, actual_cost_usd,
                cost_cap_usd, fallback_applied, reason, warnings,
                policy_action, policy_reasons, status
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(request_id) DO UPDATE SET
                timestamp = excluded.timestamp,
                organization = excluded.organization,
                team = excluded.team,
                project = excluded.project,
                endpoint = excluded.endpoint,
                selected_tier = excluded.selected_tier,
                selected_provider = excluded.selected_provider,
                selected_model = excluded.selected_model,
                requested_model = excluded.requested_model,
                complexity_score = excluded.complexity_score,
                complexity_signals = excluded.complexity_signals,
                estimated_input_tokens = excluded.estimated_input_tokens,
                estimated_output_tokens = excluded.estimated_output_tokens,
                estimated_cost_usd = excluded.estimated_cost_usd,
                actual_cost_usd = excluded.actual_cost_usd,
                cost_cap_usd = excluded.cost_cap_usd,
                fallback_applied = excluded.fallback_applied,
                reason = excluded.reason,
                warnings = excluded.warnings,
                policy_action = excluded.policy_action,
                policy_reasons = excluded.policy_reasons,
                status = excluded.status
            """,
            params,
        )

    def _spend_since(
        self,
        conn: sqlite3.Connection,
        where: list[str],
        params: list[object],
    ) -> float:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(estimated_cost_usd), 0)
            FROM request_audit
            WHERE {" AND ".join(where)}
            """,
            params,
        ).fetchone()
        value = row[0] if row is not None else 0
        return float(value)

    def spend_summary(self) -> dict[str, object]:
        """Return aggregate spend suitable for dashboards and APIs."""

        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        hour = now - timedelta(hours=1)
        return {
            "today_usd": self._sum_since(today),
            "month_usd": self._sum_since(month),
            "last_hour_usd": self._sum_since(hour),
            "by_project": self._group_sum("project", month),
            "by_team": self._group_sum("team", month),
            "by_provider": self._group_sum("selected_provider", month),
            "by_model": self._group_sum("selected_model", month),
        }

    def recent_decisions(self, limit: int = 100) -> list[dict[str, object]]:
        """Return recent routing decisions without prompt content."""

        bounded_limit = max(1, min(limit, 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, timestamp, organization, team, project, endpoint,
                       selected_tier, selected_provider, selected_model, requested_model,
                       complexity_score, estimated_input_tokens, estimated_output_tokens,
                       estimated_cost_usd, actual_cost_usd, cost_cap_usd,
                       fallback_applied, reason, warnings, policy_action,
                       policy_reasons, status
                FROM request_audit
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def _sum_since(self, since: datetime) -> float:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(estimated_cost_usd), 0)
                FROM request_audit
                WHERE timestamp >= ? AND status IN ('accepted', 'completed')
                """,
                (since.isoformat(),),
            ).fetchone()
        value = row[0] if row is not None else 0
        return round(float(value), 8)

    def _group_sum(self, column: str, since: datetime) -> dict[str, float]:
        allowed_columns = {"project", "team", "selected_provider", "selected_model"}
        if column not in allowed_columns:
            raise ValueError(f"unsupported summary column: {column}")

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {column}, COALESCE(SUM(estimated_cost_usd), 0) AS spend
                FROM request_audit
                WHERE timestamp >= ? AND status IN ('accepted', 'completed')
                GROUP BY {column}
                ORDER BY spend DESC
                """,
                (since.isoformat(),),
            ).fetchall()

        return {str(row[0]): round(float(row[1]), 8) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        path = Path(self.path)
        if path.parent and str(path.parent) != ".":
            path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS request_audit (
                    request_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    organization TEXT NOT NULL,
                    team TEXT NOT NULL,
                    project TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    selected_tier TEXT NOT NULL,
                    selected_provider TEXT NOT NULL,
                    selected_model TEXT NOT NULL,
                    requested_model TEXT,
                    complexity_score REAL NOT NULL,
                    complexity_signals TEXT NOT NULL,
                    estimated_input_tokens INTEGER NOT NULL,
                    estimated_output_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    actual_cost_usd REAL,
                    cost_cap_usd REAL NOT NULL,
                    fallback_applied INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    warnings TEXT NOT NULL,
                    policy_action TEXT NOT NULL,
                    policy_reasons TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_request_audit_scope_time
                ON request_audit (organization, team, project, timestamp)
                """,
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_request_audit_status_time
                ON request_audit (status, timestamp)
                """,
            )

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key in row.keys():
            value: Any = row[key]
            if key in {"warnings", "policy_reasons"} and isinstance(value, str):
                payload[key] = json.loads(value)
            elif key == "fallback_applied":
                payload[key] = bool(value)
            else:
                payload[key] = value
        return payload
