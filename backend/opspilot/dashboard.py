"""Evidence-backed dashboard projection for the controlled incident console."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opspilot.adapters.kubernetes import KubernetesAdapter
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.domain.tools import DeploymentRevision, KubernetesEvent, WorkloadStatus
from opspilot.settings import Settings
from opspilot.storage.incidents import SQLiteIncidentStore


class TelemetryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    value: float


class TelemetrySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    error_rate: float | None = None
    request_rate: float | None = None
    recovery_error_rate: float | None = None
    error_rate_trend: list[TelemetryPoint] = Field(default_factory=list)
    status: str
    message: str | None = None


class BlastRadiusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    workload: str
    namespace: str
    message: str


class DashboardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str
    alert_name: str
    service: str
    observed_at: datetime
    incident_age_seconds: int = Field(ge=0)
    workload: WorkloadStatus | None = None
    deployment_history: list[DeploymentRevision] = Field(default_factory=list)
    events: list[KubernetesEvent] = Field(default_factory=list)
    telemetry: TelemetrySnapshot
    blast_radius: BlastRadiusSnapshot
    slo_status: str
    slo_message: str
    collection_notes: list[str] = Field(default_factory=list)


class DashboardService:
    """Build a current display projection without inventing integrations or impact."""

    def __init__(
        self,
        store: SQLiteIncidentStore,
        settings: Settings,
        kubernetes: KubernetesAdapter | None = None,
        prometheus: PrometheusAdapter | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._kubernetes = kubernetes
        self._prometheus = prometheus

    def snapshot(self, incident_id: str) -> DashboardSnapshot:
        incident = self._store.incident(incident_id)
        if incident is None:
            raise KeyError(f"incident not found: {incident_id}")
        service, severity, alert_name = self._alert_context(self._store.list_evidence(incident_id))
        observed_at = datetime.now(UTC)
        created_at = incident.get("created_at")
        incident_age_seconds = 0
        if created_at:
            incident_age_seconds = max(
                0, int((observed_at - datetime.fromisoformat(created_at)).total_seconds())
            )
        notes: list[str] = []
        workload: WorkloadStatus | None = None
        history: list[DeploymentRevision] = []
        events: list[KubernetesEvent] = []
        kubernetes = self._kubernetes
        if kubernetes is None:
            try:
                kubernetes = KubernetesAdapter(allowed_namespace=self._settings.demo_namespace)
            except Exception as error:
                notes.append(f"Kubernetes telemetry is unavailable: {type(error).__name__}")
        if kubernetes is not None:
            try:
                workload = kubernetes.get_workload_status(self._settings.demo_namespace, service)
                deployment_history = kubernetes.get_deployment_history(
                    self._settings.demo_namespace, service
                )
                history = list(reversed(deployment_history))[:6]
                workload_events = kubernetes.get_events(self._settings.demo_namespace, service)
                events = list(reversed(workload_events))[:8]
            except Exception as error:
                notes.append(f"Kubernetes telemetry is unavailable: {type(error).__name__}")

        telemetry = self._telemetry(service, notes)
        return DashboardSnapshot(
            severity=severity,
            alert_name=alert_name,
            service=service,
            observed_at=observed_at,
            incident_age_seconds=incident_age_seconds,
            workload=workload,
            deployment_history=history,
            events=events,
            telemetry=telemetry,
            blast_radius=BlastRadiusSnapshot(
                status="not_inferred",
                workload=service,
                namespace=self._settings.demo_namespace,
                message=(
                    "Dependency topology is not configured in this controlled demo. "
                    "OpsPilot can confirm the affected workload only."
                ),
            ),
            slo_status="not_configured",
            slo_message=(
                "No service-level objective is configured for this controlled demo. "
                "The recovery gate is checkout 5xx rate at or below 0.01 over 15 seconds."
            ),
            collection_notes=notes,
        )

    def _telemetry(self, service: str, notes: list[str]) -> TelemetrySnapshot:
        prometheus = self._prometheus
        if prometheus is None and self._settings.prometheus_url:
            prometheus = PrometheusAdapter(self._settings.prometheus_url)
        if prometheus is None:
            return TelemetrySnapshot(
                service=service,
                status="not_configured",
                message="Set OPS_PILOT_PROMETHEUS_URL to show current controlled telemetry.",
            )
        try:
            error_rate = prometheus.get_metric("service_5xx_rate", service)
            request_rate = prometheus.get_metric("service_request_rate", service)
            recovery_rate = prometheus.get_metric("service_5xx_recovery_rate", service)
            trend = prometheus.get_metric_series("service_5xx_chart_rate", service)
            return TelemetrySnapshot(
                service=service,
                error_rate=round(error_rate.value, 3),
                request_rate=round(request_rate.value, 3),
                recovery_error_rate=round(recovery_rate.value, 3),
                error_rate_trend=[
                    TelemetryPoint(observed_at=observed_at, value=round(value, 3))
                    for observed_at, value in trend
                ],
                status="live",
            )
        except Exception as error:
            notes.append(f"Prometheus telemetry is unavailable: {type(error).__name__}")
            return TelemetrySnapshot(
                service=service,
                status="unavailable",
                message="The controlled Prometheus endpoint did not return telemetry.",
            )

    @staticmethod
    def _alert_context(records: list[EvidenceRecord]) -> tuple[str, str, str]:
        alert = next(
            (item for item in records if item.source_type is EvidenceSourceType.ALERT), None
        )
        if alert is None:
            return "checkout", "unknown", "No alert evidence"
        payload: dict[str, Any] = alert.structured_payload
        labels = payload.get("commonLabels", {})
        if not isinstance(labels, dict):
            labels = {}
        return (
            str(labels.get("service", "checkout")),
            str(labels.get("severity", "unknown")),
            str(labels.get("alertname", alert.summary)),
        )
