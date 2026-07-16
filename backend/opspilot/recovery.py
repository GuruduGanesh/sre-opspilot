"""Independent, deterministic recovery checks for controlled remediations."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from opspilot.domain.actions import ActionPlan, ActionType
from opspilot.domain.tools import MetricQueryResult, WorkloadStatus


class WorkloadHealthReader(Protocol):
    def get_workload_status(self, namespace: str, workload: str) -> WorkloadStatus: ...


class ServiceIndicatorReader(Protocol):
    def get_metric(self, query_name: str, service: str) -> MetricQueryResult: ...


class RecoveryResult(BaseModel):
    """Server-generated result; model output never determines recovery."""

    model_config = ConfigDict(extra="forbid")

    recovered: bool
    reason: str = Field(min_length=1, max_length=500)
    workload: WorkloadStatus
    service_5xx_rate: float | None = Field(default=None, ge=0)


class RecoveryVerifier:
    """Checks readiness and required service indicators outside the agent loop."""

    def __init__(
        self,
        workloads: WorkloadHealthReader,
        indicators: ServiceIndicatorReader | None = None,
        max_5xx_rate: float = 0.01,
    ) -> None:
        self._workloads = workloads
        self._indicators = indicators
        self._max_5xx_rate = max_5xx_rate

    def verify(self, plan: ActionPlan) -> RecoveryResult:
        workload = self._workloads.get_workload_status(
            plan.proposal.namespace, plan.proposal.workload
        )
        if workload.ready_replicas < workload.desired_replicas or workload.desired_replicas < 1:
            return RecoveryResult(
                recovered=False,
                reason="workload has not reached its desired ready replica count",
                workload=workload,
            )

        # P1 changes a response mode, so pod readiness alone is insufficient evidence.
        if plan.proposal.action_type is ActionType.ROLLBACK:
            if self._indicators is None:
                return RecoveryResult(
                    recovered=False,
                    reason="rollback recovery requires a configured 5xx service indicator",
                    workload=workload,
                )
            metric = self._indicators.get_metric("service_5xx_recovery_rate", "checkout")
            if metric.value > self._max_5xx_rate:
                return RecoveryResult(
                    recovered=False,
                    reason=(
                        "checkout 5xx rate remains above the recovery threshold "
                        f"({metric.value:.3f} > {self._max_5xx_rate:.3f})"
                    ),
                    workload=workload,
                    service_5xx_rate=metric.value,
                )
            return RecoveryResult(
                recovered=True,
                reason="workload is ready and checkout 5xx rate is within the recovery threshold",
                workload=workload,
                service_5xx_rate=metric.value,
            )

        return RecoveryResult(
            recovered=True,
            reason="workload has reached its desired ready replica count",
            workload=workload,
        )
