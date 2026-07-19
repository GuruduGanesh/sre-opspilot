"""Evidence-backed dashboard projection for the controlled incident console."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from opspilot.adapters.kubernetes import KubernetesAdapter
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.domain.evidence import EvidenceRecord, EvidenceSourceType
from opspilot.domain.incidents import LifecycleState
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
    method: str = "GET"
    route: str = "/checkout"
    rate_window: str = "1m"
    recovery_window: str = "15s"
    error_rate: float | None = None
    success_rate: float | None = None
    request_rate: float | None = None
    recovery_error_rate: float | None = None
    recovery_success_rate: float | None = None
    error_ratio: float | None = Field(default=None, ge=0, le=1)
    recovery_state: str = "unknown"
    error_rate_trend: list[TelemetryPoint] = Field(default_factory=list)
    success_rate_trend: list[TelemetryPoint] = Field(default_factory=list)
    status: str
    message: str | None = None


class BlastRadiusSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    workload: str
    namespace: str
    method: str
    route: str
    configured_callers: list[str] = Field(default_factory=list)
    downstream_dependencies: list[str] = Field(default_factory=list)
    message: str


class ServiceContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    workload: str
    image: str | None = None
    revision: str | None = None
    revision_observed_at: datetime | None = None
    controlled_config: dict[str, str] = Field(default_factory=dict)
    ready_replicas: int | None = None
    desired_replicas: int | None = None


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
    service_context: ServiceContextSnapshot
    situation_summary: str
    next_step: str
    slo_status: str
    slo_message: str
    collection_notes: list[str] = Field(default_factory=list)
    investigation_mode: str = "live_model"


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
                history = sorted(
                    deployment_history, key=lambda item: int(item.revision), reverse=True
                )[:6]
                workload_events = kubernetes.get_events(self._settings.demo_namespace, service)
                events = sorted(
                    workload_events, key=lambda item: item.observed_at, reverse=True
                )[:8]
            except Exception as error:
                notes.append(f"Kubernetes telemetry is unavailable: {type(error).__name__}")

        telemetry = self._telemetry(service, notes)
        lifecycle_state = LifecycleState(incident["lifecycle_state"])
        service_context = self._service_context(service, workload, history)
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
                status="declared_controlled_topology",
                workload=service,
                namespace=self._settings.demo_namespace,
                method="GET",
                route="/checkout",
                configured_callers=["load-generator → GET /checkout"],
                downstream_dependencies=[],
                message=(
                    "This is the declared traffic path for the controlled simulation. "
                    "OpsPilot has not inferred external callers or downstream impact."
                ),
            ),
            service_context=service_context,
            situation_summary=self._situation_summary(service, telemetry, incident_age_seconds),
            next_step=self._next_step(lifecycle_state),
            slo_status="not_configured",
            slo_message=(
                "No service-level objective is configured for this controlled demo. "
                "The recovery gate requires checkout 5xx at or below 0.01/s and 2xx at or above "
                "0.01/s over 15 seconds."
            ),
            collection_notes=notes,
            investigation_mode=(
                "controlled_simulation"
                if self._settings.simulation_investigation_enabled
                else "live_model"
            ),
        )

    def _telemetry(self, service: str, notes: list[str]) -> TelemetrySnapshot:
        prometheus = self._prometheus
        dashboard_owned_adapter = False
        if prometheus is None and self._settings.prometheus_url:
            prometheus = PrometheusAdapter(self._settings.prometheus_url)
            dashboard_owned_adapter = True
        if prometheus is None:
            return TelemetrySnapshot(
                service=service,
                status="not_configured",
                message="Set OPS_PILOT_PROMETHEUS_URL to show current controlled telemetry.",
            )
        try:
            for attempt in range(2):
                try:
                    return self._collect_telemetry(prometheus, service)
                except httpx.TransportError:
                    if attempt == 1:
                        raise
            raise RuntimeError("telemetry collection exhausted without a result")
        except Exception as error:
            notes.append(f"Prometheus telemetry is unavailable: {type(error).__name__}")
            return TelemetrySnapshot(
                service=service,
                status="unavailable",
                message="The controlled Prometheus endpoint did not return telemetry.",
            )
        finally:
            if dashboard_owned_adapter:
                prometheus.close()

    def _collect_telemetry(self, prometheus: PrometheusAdapter, service: str) -> TelemetrySnapshot:
        """Collect the independent Prometheus reads concurrently.

        A dashboard refresh needs five current values and two bounded chart series.
        They are all server-owned queries against the same point in time, so waiting
        for them one-by-one makes a transient local port-forward delay look like a
        slow or blank console. Concurrent collection keeps the UI responsive while
        still failing the whole snapshot honestly if the endpoint is unavailable.
        """

        with ThreadPoolExecutor(max_workers=7, thread_name_prefix="opspilot-telemetry") as pool:
            error_rate_future = pool.submit(prometheus.get_metric, "service_5xx_rate", service)
            success_rate_future = pool.submit(prometheus.get_metric, "service_2xx_rate", service)
            request_rate_future = pool.submit(
                prometheus.get_metric, "service_request_rate", service
            )
            recovery_rate_future = pool.submit(
                prometheus.get_metric, "service_5xx_recovery_rate", service
            )
            recovery_success_rate_future = pool.submit(
                prometheus.get_metric, "service_2xx_recovery_rate", service
            )
            failure_trend_future = pool.submit(
                prometheus.get_metric_series, "service_5xx_chart_rate", service
            )
            success_trend_future = pool.submit(
                prometheus.get_metric_series, "service_2xx_chart_rate", service
            )
            error_rate = error_rate_future.result()
            success_rate = success_rate_future.result()
            request_rate = request_rate_future.result()
            recovery_rate = recovery_rate_future.result()
            recovery_success_rate = recovery_success_rate_future.result()
            failure_trend = failure_trend_future.result()
            success_trend = success_trend_future.result()
        return TelemetrySnapshot(
            service=service,
            error_rate=round(error_rate.value, 3),
            success_rate=round(success_rate.value, 3),
            request_rate=round(request_rate.value, 3),
            recovery_error_rate=round(recovery_rate.value, 3),
            recovery_success_rate=round(recovery_success_rate.value, 3),
            error_ratio=(
                round(error_rate.value / request_rate.value, 4) if request_rate.value > 0 else None
            ),
            recovery_state=(
                "passing"
                if (
                    recovery_rate.value <= self._settings.recovery_max_5xx_rate
                    and recovery_success_rate.value >= self._settings.recovery_min_2xx_rate
                )
                else "failing"
            ),
            error_rate_trend=[
                TelemetryPoint(observed_at=observed_at, value=round(value, 3))
                for observed_at, value in failure_trend
            ],
            success_rate_trend=[
                TelemetryPoint(observed_at=observed_at, value=round(value, 3))
                for observed_at, value in success_trend
            ],
            status="live",
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

    def _service_context(
        self,
        service: str,
        workload: WorkloadStatus | None,
        history: list[DeploymentRevision],
    ) -> ServiceContextSnapshot:
        latest = max(history, key=lambda item: int(item.revision)) if history else None
        return ServiceContextSnapshot(
            namespace=self._settings.demo_namespace,
            workload=service,
            image=latest.images[0] if latest and latest.images else None,
            revision=latest.revision if latest else None,
            revision_observed_at=latest.observed_at if latest else None,
            controlled_config=latest.controlled_config if latest else {},
            ready_replicas=workload.ready_replicas if workload else None,
            desired_replicas=workload.desired_replicas if workload else None,
        )

    def _situation_summary(
        self, service: str, telemetry: TelemetrySnapshot, incident_age_seconds: int
    ) -> str:
        if telemetry.status != "live":
            return (
                "Current route telemetry is unavailable; do not infer the incident scope "
                "from stale data."
            )
        if telemetry.error_ratio is None:
            return (
                f"No requests were observed for {service} {telemetry.method} {telemetry.route} "
                f"in the last {telemetry.rate_window}."
            )
        recovery_note = ""
        if (
            telemetry.recovery_state == "passing"
            and telemetry.error_rate is not None
            and telemetry.error_rate > self._settings.recovery_max_5xx_rate
        ):
            recovery_note = (
                " The 15-second recovery gate is passing, while the 1-minute ratio still "
                "includes earlier failures."
            )
        return (
            f"Currently {telemetry.error_ratio:.1%} of observed {service} {telemetry.method} "
            f"{telemetry.route} requests are HTTP 5xx over the last {telemetry.rate_window}. "
            f"The incident record was received {self._human_age(incident_age_seconds)} ago."
            f"{recovery_note}"
        )

    @staticmethod
    def _human_age(seconds: int) -> str:
        minutes, remaining = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {remaining}s"
        return f"{remaining}s"

    @staticmethod
    def _next_step(lifecycle_state: LifecycleState) -> str:
        steps = {
            LifecycleState.RECEIVED: (
                "Classify and enrich the alert before proposing any action."
            ),
            LifecycleState.CLASSIFIED: (
                "Collect Kubernetes and Prometheus evidence for the affected route."
            ),
            LifecycleState.ENRICHED: "Review the collected evidence and begin triage.",
            LifecycleState.TRIAGING: (
                "Run a fresh investigation, then create a dry-run preview only for an "
                "allowlisted restoration."
            ),
            LifecycleState.ACTION_PROPOSED: (
                "Review the dry-run preview and explicitly approve or reject that exact plan."
            ),
            LifecycleState.EXECUTING: (
                "Wait for the approved controlled action to complete; do not assume recovery."
            ),
            LifecycleState.MONITORING: (
                "Independent recovery verification is checking workload readiness, the 15-second "
                "5xx threshold, and observed 2xx traffic."
            ),
            LifecycleState.RESOLVED: (
                "Recovery is verified. Draft the factual RCA from the persisted audit trail."
            ),
            LifecycleState.RCA: (
                "Review the factual RCA draft, then publish it when the incident record is "
                "complete."
            ),
            LifecycleState.RCA_PUBLISHED: (
                "Incident workflow complete; retain the audit record for review."
            ),
        }
        return steps[lifecycle_state]
