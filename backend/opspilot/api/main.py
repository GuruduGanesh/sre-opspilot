import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from opspilot.adapters.kubernetes import KubernetesAdapter
from opspilot.adapters.prometheus import PrometheusAdapter
from opspilot.dashboard import DashboardService, DashboardSnapshot
from opspilot.domain.actions import ActionPlan, ActionType
from opspilot.domain.alerts import AlertmanagerWebhookV4
from opspilot.domain.evidence import EvidenceRecord
from opspilot.domain.incidents import LifecycleState
from opspilot.domain.investigation import InvestigationReport
from opspilot.investigation import InvestigationWorkflow
from opspilot.postmortem import PostmortemDraft, PostmortemService
from opspilot.recovery import RecoveryResult, RecoveryVerifier
from opspilot.remediation import KubernetesRemediationAdapter, RemediationCoordinator
from opspilot.settings import Settings
from opspilot.simulation import DemoScenarioService, DemoScenarioStart
from opspilot.storage.incidents import SQLiteIncidentStore

_PUBLIC_LIFECYCLE_TARGETS = {
    LifecycleState.CLASSIFIED,
    LifecycleState.ENRICHED,
    LifecycleState.TRIAGING,
}


class IngestResponse(BaseModel):
    incident_id: str | None
    disposition: str


class LifecycleTransitionRequest(BaseModel):
    """Internal server command; the future agent may suggest, never apply, this."""

    model_config = ConfigDict(extra="forbid")

    target: LifecycleState
    actor: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1_000)


class ActionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    target_replicas: int | None = Field(default=None, ge=1, le=3)
    requested_by: str = Field(default="local-oncall", min_length=3, max_length=128)


class ActionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(min_length=3, max_length=128)


class ActionRejectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rejected_by: str = Field(min_length=3, max_length=128)
    reason: str | None = Field(default=None, max_length=500)


class VerificationResponse(BaseModel):
    plan: ActionPlan
    recovery: RecoveryResult


class IncidentQueueItem(BaseModel):
    """Minimal current incident metadata for the controlled on-call queue."""

    model_config = ConfigDict(extra="forbid")

    id: str
    lifecycle_state: LifecycleState
    created_at: str
    updated_at: str
    severity: str
    service: str
    alert_name: str


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings()
    store = SQLiteIncidentStore(runtime_settings.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store.initialize()
        yield

    app = FastAPI(title="OpsPilot API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-OpsPilot-Scenario-Secret"],
    )
    app.state.store = store

    def remediation() -> RemediationCoordinator:
        return RemediationCoordinator(
            store,
            KubernetesRemediationAdapter(
                allowed_namespace=runtime_settings.demo_namespace,
                allowed_workloads={"checkout"},
            ),
            namespace=runtime_settings.demo_namespace,
            workload="checkout",
            recovery_max_5xx_rate=runtime_settings.recovery_max_5xx_rate,
        )

    def action_or_404(action_id: str) -> ActionPlan:
        plan = store.action_plan(action_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="action plan not found"
            )
        remediation().list_for_incident(plan.proposal.incident_id)
        return store.action_plan(action_id) or plan

    def postmortem_or_404(incident_id: str) -> PostmortemDraft:
        incident = store.incident(incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        return PostmortemService().create(
            incident,
            store.list_evidence(incident_id),
            store.list_action_plans(incident_id),
            store.timeline(incident_id),
            store.latest_investigation(incident_id),
        )

    def queue_item(incident: dict[str, str]) -> IncidentQueueItem:
        service, severity, alert_name = DashboardService._alert_context(  # noqa: SLF001
            store.list_evidence(incident["id"])
        )
        return IncidentQueueItem(
            id=incident["id"],
            lifecycle_state=LifecycleState(incident["lifecycle_state"]),
            created_at=incident["created_at"],
            updated_at=incident["updated_at"],
            severity=severity,
            service=service,
            alert_name=alert_name,
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "investigation_mode": (
                "controlled_simulation"
                if runtime_settings.simulation_investigation_enabled
                else "live_model"
            ),
        }

    @app.post(
        "/api/v1/ingress/alertmanager",
        response_model=IngestResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ingest_alert(payload: AlertmanagerWebhookV4, request: Request) -> IngestResponse:
        if runtime_settings.alert_shared_secret:
            supplied = request.headers.get("X-OpsPilot-Scenario-Secret")
            if supplied != runtime_settings.alert_shared_secret:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret"
                )
        result = store.ingest(payload)
        return IngestResponse(incident_id=result.incident_id, disposition=result.disposition)

    @app.get("/api/v1/incidents", response_model=list[IncidentQueueItem])
    def list_incidents(state: str = "open") -> list[IncidentQueueItem]:
        if state not in {"open", "all"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="state must be 'open' or 'all'",
            )
        severity_rank = {"critical": 0, "warning": 1, "unknown": 2}
        records = store.list_incidents(open_only=state == "open")
        items = [queue_item(incident) for incident in records]
        return sorted(
            items,
            key=lambda item: (
                severity_rank.get(item.severity.lower(), 3),
                -datetime.fromisoformat(item.updated_at).timestamp(),
            ),
        )

    @app.get("/api/v1/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, str]:
        incident = store.incident(incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        return incident

    @app.get("/api/v1/incidents/{incident_id}/evidence", response_model=list[EvidenceRecord])
    def get_evidence(incident_id: str) -> list[EvidenceRecord]:
        if store.incident(incident_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        return store.list_evidence(incident_id)

    @app.get("/api/v1/incidents/{incident_id}/timeline")
    def get_timeline(incident_id: str) -> list[dict[str, str]]:
        try:
            return store.timeline(incident_id)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            ) from error

    @app.get(
        "/api/v1/incidents/{incident_id}/investigation",
        response_model=InvestigationReport | None,
    )
    def get_latest_investigation(incident_id: str) -> InvestigationReport | None:
        if store.incident(incident_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        return store.latest_investigation(incident_id)

    @app.get("/api/v1/incidents/{incident_id}/dashboard", response_model=DashboardSnapshot)
    def dashboard(incident_id: str) -> DashboardSnapshot:
        try:
            remediation().list_for_incident(incident_id)
            return DashboardService(store, runtime_settings).snapshot(incident_id)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            ) from error

    @app.post("/api/v1/demo/scenarios/{scenario}", response_model=DemoScenarioStart)
    def start_demo_scenario(scenario: str) -> DemoScenarioStart:
        if not runtime_settings.demo_controls_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="local demo controls are disabled",
            )
        try:
            return DemoScenarioService(store, runtime_settings.demo_namespace).start(scenario)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(error),
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "controlled Kubernetes simulation is unavailable; "
                    "create the local scenario first"
                ),
            ) from error

    @app.post("/api/v1/demo/reset", status_code=status.HTTP_204_NO_CONTENT)
    def reset_demo_scenario() -> None:
        if not runtime_settings.demo_controls_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="local demo controls are disabled",
            )
        try:
            DemoScenarioService(store, runtime_settings.demo_namespace).reset()
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "controlled Kubernetes simulation is unavailable; "
                    "create the local scenario first"
                ),
            ) from error

    @app.get("/api/v1/incidents/{incident_id}/events")
    async def incident_events(incident_id: str) -> StreamingResponse:
        if store.incident(incident_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )

        async def stream() -> AsyncIterator[str]:
            previous: str | None = None
            while True:
                incident = store.incident(incident_id)
                if incident is None:
                    return
                payload = json.dumps(
                    {"incident": incident, "timeline": store.timeline(incident_id)},
                    sort_keys=True,
                )
                if payload != previous:
                    yield f"event: incident\ndata: {payload}\n\n"
                    previous = payload
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/incidents/{incident_id}/postmortem", response_model=PostmortemDraft)
    def postmortem(incident_id: str) -> PostmortemDraft:
        return postmortem_or_404(incident_id)

    @app.post("/api/v1/incidents/{incident_id}/postmortem/draft", response_model=PostmortemDraft)
    def draft_postmortem(incident_id: str) -> PostmortemDraft:
        incident = store.incident(incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        if incident["lifecycle_state"] == LifecycleState.RESOLVED.value:
            store.transition(
                incident_id,
                LifecycleState.RCA,
                actor="opspilot-postmortem",
                reason="opened audit-derived RCA draft for engineer review",
            )
        elif incident["lifecycle_state"] != LifecycleState.RCA.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="RCA draft is available only after recovery is verified",
            )
        return postmortem_or_404(incident_id)

    @app.post("/api/v1/incidents/{incident_id}/postmortem/publish")
    def publish_postmortem(incident_id: str) -> dict[str, str]:
        incident = store.incident(incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        if incident["lifecycle_state"] != LifecycleState.RCA.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="review the RCA draft before marking it published",
            )
        return store.transition(
            incident_id,
            LifecycleState.RCA_PUBLISHED,
            actor="oncall-engineer",
            reason="engineer marked the audit-derived RCA as published in the controlled demo",
        )

    @app.post("/api/v1/incidents/{incident_id}/lifecycle")
    def transition_lifecycle(
        incident_id: str, request: LifecycleTransitionRequest
    ) -> dict[str, str]:
        if request.target not in _PUBLIC_LIFECYCLE_TARGETS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="protected lifecycle transitions require the server-side workflow",
            )
        try:
            return store.transition(incident_id, request.target, request.actor, request.reason)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/v1/incidents/{incident_id}/investigate", response_model=InvestigationReport)
    def investigate_incident(
        incident_id: str, request: InvestigationRequest
    ) -> InvestigationReport:
        try:
            return InvestigationWorkflow(store, runtime_settings).investigate(
                incident_id, request.question
            )
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="investigation model is unavailable; check server-side configuration",
            ) from error

    @app.post("/api/v1/incidents/{incident_id}/actions/preview", response_model=ActionPlan)
    def preview_action(incident_id: str, request: ActionPreviewRequest) -> ActionPlan:
        try:
            return remediation().propose(
                incident_id,
                request.action_type,
                request.evidence_ids,
                request.target_replicas,
                request.requested_by,
            )
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="remediation preview is unavailable in the local Kubernetes environment",
            ) from error

    @app.get("/api/v1/actions/{action_id}", response_model=ActionPlan)
    def get_action(action_id: str) -> ActionPlan:
        return action_or_404(action_id)

    @app.get("/api/v1/incidents/{incident_id}/actions", response_model=list[ActionPlan])
    def list_incident_actions(incident_id: str) -> list[ActionPlan]:
        if store.incident(incident_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            )
        return remediation().list_for_incident(incident_id)

    @app.post("/api/v1/actions/{action_id}/approve", response_model=ActionPlan)
    def approve_action(action_id: str, request: ActionApprovalRequest) -> ActionPlan:
        try:
            return remediation().approve(action_id, request.approved_by)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="action plan not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/v1/actions/{action_id}/reject", response_model=ActionPlan)
    def reject_action(action_id: str, request: ActionRejectionRequest) -> ActionPlan:
        try:
            return remediation().reject(action_id, request.rejected_by, request.reason)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="action plan not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.post("/api/v1/actions/{action_id}/execute", response_model=ActionPlan)
    def execute_action(action_id: str) -> ActionPlan:
        try:
            return remediation().execute(action_id)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="action plan not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="remediation execution failed in the local Kubernetes environment",
            ) from error

    @app.post("/api/v1/actions/{action_id}/verify", response_model=VerificationResponse)
    def verify_action(action_id: str) -> VerificationResponse:
        plan = action_or_404(action_id)
        if (
            plan.proposal.action_type.value == "restore_response_mode"
            and not runtime_settings.prometheus_url
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="set OPS_PILOT_PROMETHEUS_URL before recovery verification",
            )
        indicators = (
            PrometheusAdapter(runtime_settings.prometheus_url)
            if runtime_settings.prometheus_url
            else None
        )
        verifier = RecoveryVerifier(
            KubernetesAdapter(allowed_namespace=runtime_settings.demo_namespace),
            indicators,
            max_5xx_rate=runtime_settings.recovery_max_5xx_rate,
        )
        try:
            plan, recovery = remediation().verify(action_id, verifier)
            return VerificationResponse(plan=plan, recovery=recovery)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="action plan not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="recovery verification is unavailable in the local Kubernetes environment",
            ) from error

    return app


app = create_app()
