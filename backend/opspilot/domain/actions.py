from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    ROLLBACK = "rollback"
    RESTART = "restart"
    SCALE = "scale"


class ActionProposal(BaseModel):
    """Canonical proposal created by the server, not executable model text."""

    model_config = ConfigDict(extra="forbid")

    incident_id: str
    action_type: ActionType
    namespace: str
    workload: str
    evidence_ids: list[str] = Field(min_length=1)
    expected_resource_version: str = Field(min_length=1)
    expires_at: datetime
    target_replicas: int | None = Field(default=None, ge=1, le=3)


class ActionPolicy:
    """First safety gate. Execution and Kubernetes dry-run are separate layers."""

    def __init__(self, namespace: str = "opspilot-demo", workloads: set[str] | None = None) -> None:
        self._namespace = namespace
        self._workloads = workloads or {"checkout"}

    def validate_for_preview(self, proposal: ActionProposal, now: datetime | None = None) -> None:
        current_time = now or datetime.now(UTC)
        if proposal.namespace != self._namespace:
            raise ValueError("action target is outside the dedicated demo namespace")
        if proposal.workload not in self._workloads:
            raise ValueError("workload is not allowlisted")
        if proposal.expires_at <= current_time:
            raise ValueError("action proposal is stale and requires a new preview")
        if proposal.action_type is ActionType.SCALE and proposal.target_replicas is None:
            raise ValueError("scale requires a bounded target replica count")
        if proposal.action_type is not ActionType.SCALE and proposal.target_replicas is not None:
            raise ValueError("only scale may include target replicas")

    def validate_for_execution(
        self,
        proposal: ActionProposal,
        approved_at: datetime | None,
        current_resource_version: str,
        now: datetime | None = None,
    ) -> None:
        self.validate_for_preview(proposal, now=now)
        if approved_at is None:
            raise ValueError("explicit human approval is required before execution")
        if proposal.expected_resource_version != current_resource_version:
            raise ValueError("action proposal is stale because the target changed")
