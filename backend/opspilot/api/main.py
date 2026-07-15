from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from opspilot.domain.alerts import AlertmanagerWebhookV4
from opspilot.domain.evidence import EvidenceRecord
from opspilot.domain.incidents import LifecycleState
from opspilot.settings import Settings
from opspilot.storage.incidents import SQLiteIncidentStore


class IngestResponse(BaseModel):
    incident_id: str | None
    disposition: str


class LifecycleTransitionRequest(BaseModel):
    """Internal server command; the future agent may suggest, never apply, this."""

    model_config = ConfigDict(extra="forbid")

    target: LifecycleState
    actor: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=500)


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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

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

    @app.post("/api/v1/incidents/{incident_id}/lifecycle")
    def transition_lifecycle(
        incident_id: str, request: LifecycleTransitionRequest
    ) -> dict[str, str]:
        try:
            return store.transition(incident_id, request.target, request.actor, request.reason)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="incident not found"
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return app


app = create_app()
