"""Collect a bounded, auditable live evidence snapshot for one investigation."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from opspilot.dashboard import DashboardService, DashboardSnapshot
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.settings import Settings
from opspilot.storage.incidents import SQLiteIncidentStore


class LiveEvidenceCollector:
    """Persist only current, source-backed context needed for an investigation."""

    def __init__(
        self,
        store: SQLiteIncidentStore,
        settings: Settings,
        dashboard: DashboardService | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._dashboard = dashboard or DashboardService(store, settings)

    def collect(self, incident_id: str) -> list[EvidenceRecord]:
        snapshot = self._dashboard.snapshot(incident_id)
        collected_at = datetime.now(UTC)
        records = self._records(incident_id, snapshot, collected_at)
        return [record for record in records if self._store.append_evidence_if_new(record)]

    def _records(
        self,
        incident_id: str,
        snapshot: DashboardSnapshot,
        collected_at: datetime,
    ) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        if snapshot.telemetry.status == "live":
            telemetry = snapshot.telemetry.model_dump(mode="json")
            records.append(
                self._record(
                    incident_id,
                    EvidenceSourceType.PROMETHEUS,
                    f"prometheus:dashboard:{snapshot.service}",
                    collected_at,
                    (
                        f"Current {snapshot.service} {snapshot.telemetry.method} "
                        f"{snapshot.telemetry.route} telemetry: "
                        f"5xx {snapshot.telemetry.error_rate:.3f}/s, "
                        f"requests {snapshot.telemetry.request_rate:.3f}/s "
                        f"over {snapshot.telemetry.rate_window}, recovery 5xx "
                        f"{snapshot.telemetry.recovery_error_rate:.3f}/s "
                        f"over {snapshot.telemetry.recovery_window}"
                    ),
                    telemetry,
                )
            )
        if snapshot.workload is not None:
            workload = snapshot.workload.model_dump(mode="json")
            records.append(
                self._record(
                    incident_id,
                    EvidenceSourceType.KUBERNETES,
                    f"kubernetes:workload:{snapshot.workload.namespace}/{snapshot.workload.workload}",
                    snapshot.workload.observed_at,
                    (
                        f"Workload {snapshot.workload.namespace}/{snapshot.workload.workload}: "
                        f"{snapshot.workload.ready_replicas}/"
                        f"{snapshot.workload.desired_replicas} ready, "
                        f"{snapshot.workload.restart_count} restarts"
                    ),
                    workload,
                )
            )
        for event in snapshot.events:
            event_payload = event.model_dump(mode="json")
            records.append(
                self._record(
                    incident_id,
                    EvidenceSourceType.KUBERNETES,
                    f"kubernetes:event:{event.involved_object}:{event.reason}",
                    event.observed_at,
                    f"Kubernetes {event.event_type} {event.reason}: {event.message}",
                    event_payload,
                )
            )
        for revision in snapshot.deployment_history:
            revision_payload = revision.model_dump(mode="json")
            records.append(
                self._record(
                    incident_id,
                    EvidenceSourceType.DEPLOYMENT,
                    f"kubernetes:deployment:{snapshot.service}:revision:{revision.revision}",
                    revision.observed_at,
                    (
                        f"Deployment revision {revision.revision} for {snapshot.service}: "
                        f"{', '.join(revision.images) or 'no image recorded'}"
                    ),
                    revision_payload,
                )
            )
        return records

    @staticmethod
    def _record(
        incident_id: str,
        source_type: EvidenceSourceType,
        source_ref: str,
        observed_at: datetime,
        summary: str,
        payload: dict[str, object],
    ) -> EvidenceRecord:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return EvidenceRecord(
            id=str(uuid4()),
            incident_id=incident_id,
            source_type=source_type,
            source_ref=source_ref,
            observed_at=observed_at,
            summary=summary,
            structured_payload=payload,
            content_hash=sha256(canonical.encode()).hexdigest(),
        )
