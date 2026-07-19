import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActionType(StrEnum):
    RESTORE_RESPONSE_MODE = "restore_response_mode"
    RESTORE_MEMORY_MODE = "restore_memory_mode"
    RESTART = "restart"
    SCALE = "scale"


class ActionPlanStatus(StrEnum):
    PREVIEWED = "Previewed"
    APPROVED = "Approved"
    EXECUTING = "Executing"
    EXECUTED = "Executed"
    VERIFYING = "Verifying"
    VERIFIED = "Verified"
    REJECTED = "Rejected"
    FAILED = "Failed"
    EXPIRED = "Expired"
    STALE = "Stale"


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
    # The local demo does not authenticate an operator.  Keep this value visibly
    # self-declared so the audit record does not imply an identity system exists.
    requested_by: str = Field(default="local-oncall", min_length=3, max_length=128)
    proposed_at: datetime | None = None
    target_replicas: int | None = Field(default=None, ge=1, le=3)
    restart_at: datetime | None = None

    @field_validator("action_type", mode="before")
    @classmethod
    def map_legacy_rollback_name(cls, value: object) -> object:
        """Read pre-rename local plans without preserving a misleading action name."""

        return "restore_response_mode" if value == "rollback" else value


class ActionPlan(BaseModel):
    """Server-owned action object whose fingerprint binds preview and approval."""

    model_config = ConfigDict(extra="forbid")

    id: str
    proposal: ActionProposal
    preview: dict[str, object]
    fingerprint: str
    status: ActionPlanStatus
    approved_at: datetime | None = None
    approved_by: str | None = None
    executed_at: datetime | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    stability_observed_at: datetime | None = None
    stability_restart_count: int | None = Field(default=None, ge=0)
    # Persist the independent verifier result with the plan so a page reload
    # cannot turn a completed recovery decision into an unexplained status.
    recovery: dict[str, object] | None = None


def action_fingerprint(proposal: ActionProposal, preview: dict[str, object]) -> str:
    material = {
        "proposal": proposal.model_dump(mode="json"),
        "preview": preview,
    }
    return sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
