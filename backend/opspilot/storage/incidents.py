import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from opspilot.domain.actions import ActionPlan, ActionPlanStatus
from opspilot.domain.alerts import AlertmanagerWebhookV4, AlertStatus
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.domain.incidents import LifecycleState, validate_transition
from opspilot.domain.investigation import InvestigationReport


@dataclass(frozen=True)
class IngestResult:
    incident_id: str | None
    disposition: str


class SQLiteIncidentStore:
    """Transactionally ingests Alertmanager-compatible scenario alerts."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    incident_key TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident_per_key
                    ON incidents(incident_key)
                    WHERE lifecycle_state NOT IN ('Resolved', 'RCAPublished', 'Archived');
                CREATE TABLE IF NOT EXISTS alert_deliveries (
                    delivery_hash TEXT PRIMARY KEY,
                    incident_id TEXT,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE TABLE IF NOT EXISTS incident_evidence (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE TABLE IF NOT EXISTS evidence_records (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    structured_payload_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE INDEX IF NOT EXISTS evidence_by_incident
                    ON evidence_records(incident_id, observed_at);
                CREATE INDEX IF NOT EXISTS evidence_by_incident_hash
                    ON evidence_records(incident_id, content_hash);
                CREATE TABLE IF NOT EXISTS incident_transitions (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    transitioned_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE INDEX IF NOT EXISTS transitions_by_incident
                    ON incident_transitions(incident_id, transitioned_at);
                CREATE TABLE IF NOT EXISTS investigation_runs (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE TABLE IF NOT EXISTS action_plans (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id)
                );
                CREATE INDEX IF NOT EXISTS action_plans_by_incident
                    ON action_plans(incident_id, created_at);
                CREATE TABLE IF NOT EXISTS action_plan_events (
                    id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    action_status TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY(action_id) REFERENCES action_plans(id)
                );
                CREATE INDEX IF NOT EXISTS action_plan_events_by_action
                    ON action_plan_events(action_id, occurred_at);
                """
            )

    def ingest(self, payload: AlertmanagerWebhookV4) -> IngestResult:
        now = datetime.now(UTC).isoformat()
        delivery_hash = payload.delivery_hash()
        incident_key = payload.incident_key()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            retry = connection.execute(
                "SELECT incident_id FROM alert_deliveries WHERE delivery_hash = ?", (delivery_hash,)
            ).fetchone()
            if retry is not None:
                return IngestResult(incident_id=retry["incident_id"], disposition="retry_ignored")

            row = connection.execute(
                """
                SELECT id FROM incidents
                WHERE incident_key = ?
                  AND lifecycle_state NOT IN ('Resolved', 'RCAPublished', 'Archived')
                """,
                (incident_key,),
            ).fetchone()
            incident_id = row["id"] if row else None

            if payload.status is AlertStatus.RESOLVED and incident_id is None:
                connection.execute(
                    """
                    INSERT INTO alert_deliveries(delivery_hash, incident_id, received_at)
                    VALUES (?, ?, ?)
                    """,
                    (delivery_hash, None, now),
                )
                return IngestResult(incident_id=None, disposition="unmatched_resolved")

            if incident_id is None:
                incident_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO incidents(id, incident_key, lifecycle_state, created_at, updated_at)
                    VALUES (?, ?, 'Received', ?, ?)
                    """,
                    (incident_id, incident_key, now, now),
                )
                disposition = "incident_created"
            else:
                connection.execute(
                    "UPDATE incidents SET updated_at = ? WHERE id = ?", (now, incident_id)
                )
                disposition = (
                    "resolution_signal_recorded"
                    if payload.status is AlertStatus.RESOLVED
                    else "alert_update_recorded"
                )

            connection.execute(
                """
                INSERT INTO alert_deliveries(delivery_hash, incident_id, received_at)
                VALUES (?, ?, ?)
                """,
                (delivery_hash, incident_id, now),
            )
            connection.execute(
                """
                INSERT INTO incident_evidence(
                    id, incident_id, event_type, payload_json, received_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    incident_id,
                    disposition,
                    json.dumps(payload.model_dump(by_alias=True, mode="json"), sort_keys=True),
                    now,
                ),
            )
            self._append_evidence_in_connection(
                connection,
                EvidenceRecord(
                    id=str(uuid4()),
                    incident_id=incident_id,
                    source_type=EvidenceSourceType.ALERT,
                    source_ref=str(
                        payload.alerts[0].generator_url or payload.external_url or "alert"
                    ),
                    observed_at=payload.alerts[0].starts_at,
                    summary=(
                        f"Scenario alert {payload.status.value}: "
                        f"{payload.common_labels.get('alertname', 'unknown')}"
                    ),
                    structured_payload=payload.model_dump(by_alias=True, mode="json"),
                    content_hash=delivery_hash,
                ),
                collected_at=now,
            )
            return IngestResult(incident_id=incident_id, disposition=disposition)

    def incident(self, incident_id: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, incident_key, lifecycle_state, created_at, updated_at
                FROM incidents WHERE id = ?
                """,
                (incident_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_incidents(self, *, open_only: bool = True) -> list[dict[str, str]]:
        """Return recent incident records for the local triage queue."""

        where = "WHERE lifecycle_state NOT IN ('Resolved', 'RCA', 'RCAPublished', 'Archived')"
        if not open_only:
            where = ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, incident_key, lifecycle_state, created_at, updated_at
                FROM incidents
                {where}
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def append_evidence(self, record: EvidenceRecord) -> None:
        with self._connect() as connection:
            incident = connection.execute(
                "SELECT 1 FROM incidents WHERE id = ?", (record.incident_id,)
            ).fetchone()
            if incident is None:
                raise KeyError(f"incident not found: {record.incident_id}")
            self._append_evidence_in_connection(connection, record, datetime.now(UTC).isoformat())

    def append_evidence_if_new(self, record: EvidenceRecord) -> bool:
        """Append a fact only once when its source payload has not changed."""

        with self._connect() as connection:
            incident = connection.execute(
                "SELECT 1 FROM incidents WHERE id = ?", (record.incident_id,)
            ).fetchone()
            if incident is None:
                raise KeyError(f"incident not found: {record.incident_id}")
            existing = connection.execute(
                """
                SELECT 1 FROM evidence_records
                WHERE incident_id = ? AND source_ref = ? AND content_hash = ?
                """,
                (record.incident_id, record.source_ref, record.content_hash),
            ).fetchone()
            if existing is not None:
                return False
            self._append_evidence_in_connection(connection, record, datetime.now(UTC).isoformat())
            return True

    def list_evidence(self, incident_id: str) -> list[EvidenceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, incident_id, source_type, source_ref, observed_at, summary,
                       structured_payload_json, content_hash
                FROM evidence_records WHERE incident_id = ? ORDER BY observed_at, id
                """,
                (incident_id,),
            ).fetchall()
        return [
            EvidenceRecord(
                id=row["id"],
                incident_id=row["incident_id"],
                source_type=row["source_type"],
                source_ref=row["source_ref"],
                observed_at=row["observed_at"],
                summary=row["summary"],
                structured_payload=json.loads(row["structured_payload_json"]),
                content_hash=row["content_hash"],
            )
            for row in rows
        ]

    def transition(
        self, incident_id: str, target: LifecycleState, actor: str, reason: str
    ) -> dict[str, str]:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_state FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"incident not found: {incident_id}")
            current = LifecycleState(row["lifecycle_state"])
            validate_transition(current, target)
            connection.execute(
                "UPDATE incidents SET lifecycle_state = ?, updated_at = ? WHERE id = ?",
                (target.value, now, incident_id),
            )
            connection.execute(
                """
                INSERT INTO incident_transitions(
                    id, incident_id, from_state, to_state, actor, reason, transitioned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), incident_id, current.value, target.value, actor, reason, now),
            )
        incident = self.incident(incident_id)
        if incident is None:
            raise KeyError(f"incident not found after transition: {incident_id}")
        return incident

    def timeline(self, incident_id: str) -> list[dict[str, str]]:
        if self.incident(incident_id) is None:
            raise KeyError(f"incident not found: {incident_id}")
        with self._connect() as connection:
            evidence = connection.execute(
                """
                SELECT observed_at AS occurred_at, 'evidence' AS event_type, summary AS detail
                FROM evidence_records WHERE incident_id = ?
                """,
                (incident_id,),
            ).fetchall()
            transitions = connection.execute(
                """
                SELECT transitioned_at AS occurred_at, 'lifecycle' AS event_type,
                       from_state || ' -> ' || to_state || ': ' || reason AS detail
                FROM incident_transitions WHERE incident_id = ?
                """,
                (incident_id,),
            ).fetchall()
        records = sorted([*evidence, *transitions], key=lambda row: row["occurred_at"])
        return [dict(row) for row in records]

    def record_investigation(
        self, incident_id: str, model_id: str, report: InvestigationReport
    ) -> None:
        with self._connect() as connection:
            incident = connection.execute(
                "SELECT 1 FROM incidents WHERE id = ?", (incident_id,)
            ).fetchone()
            if incident is None:
                raise KeyError(f"incident not found: {incident_id}")
            connection.execute(
                """
                INSERT INTO investigation_runs(id, incident_id, model_id, report_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    incident_id,
                    model_id,
                    report.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
    def create_action_plan(self, plan: ActionPlan) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            incident = connection.execute(
                "SELECT 1 FROM incidents WHERE id = ?", (plan.proposal.incident_id,)
            ).fetchone()
            if incident is None:
                raise KeyError(f"incident not found: {plan.proposal.incident_id}")
            connection.execute(
                """
                INSERT INTO action_plans(id, incident_id, status, plan_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id,
                    plan.proposal.incident_id,
                    plan.status.value,
                    plan.model_dump_json(),
                    now,
                    now,
                ),
            )
            self._append_action_event_in_connection(connection, plan, "preview_created", now)

    def action_plan(self, action_id: str) -> ActionPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json FROM action_plans WHERE id = ?", (action_id,)
            ).fetchone()
        return ActionPlan.model_validate_json(row["plan_json"]) if row else None

    def list_action_plans(self, incident_id: str) -> list[ActionPlan]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT plan_json FROM action_plans WHERE incident_id = ? ORDER BY created_at, id",
                (incident_id,),
            ).fetchall()
        return [ActionPlan.model_validate_json(row["plan_json"]) for row in rows]

    def update_action_plan(
        self, plan: ActionPlan, expected_status: ActionPlanStatus | None = None
    ) -> None:
        with self._connect() as connection:
            now = datetime.now(UTC).isoformat()
            statement = (
                "UPDATE action_plans SET status = ?, plan_json = ?, updated_at = ? WHERE id = ?"
            )
            parameters: tuple[object, ...] = (
                plan.status.value,
                plan.model_dump_json(),
                now,
                plan.id,
            )
            if expected_status is not None:
                statement += " AND status = ?"
                parameters += (expected_status.value,)
            result = connection.execute(statement, parameters)
            if result.rowcount != 1:
                if expected_status is not None:
                    raise ValueError("action plan changed concurrently; reload before continuing")
                raise KeyError(f"action plan not found: {plan.id}")
            self._append_action_event_in_connection(connection, plan, "status_changed", now)

    def action_audit(self, action_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, action_status, occurred_at
                FROM action_plan_events WHERE action_id = ? ORDER BY rowid
                """,
                (action_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _append_evidence_in_connection(
        connection: sqlite3.Connection,
        record: EvidenceRecord,
        collected_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_records(
                id, incident_id, source_type, source_ref, observed_at, summary,
                structured_payload_json, content_hash, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.incident_id,
                record.source_type.value,
                record.source_ref,
                record.observed_at.isoformat(),
                record.summary,
                json.dumps(record.structured_payload, sort_keys=True),
                record.content_hash,
                collected_at,
            ),
        )

    @staticmethod
    def _append_action_event_in_connection(
        connection: sqlite3.Connection,
        plan: ActionPlan,
        event_type: str,
        occurred_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO action_plan_events(
                id, action_id, event_type, action_status, plan_json, occurred_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                plan.id,
                event_type,
                plan.status.value,
                plan.model_dump_json(),
                occurred_at,
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
