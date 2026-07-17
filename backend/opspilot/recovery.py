"""Independent, deterministic recovery checks for controlled remediations."""

from datetime import UTC, datetime
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
    pending: bool = False
    stability_window_remaining_seconds: int | None = Field(default=None, ge=0)
    stability_observed_at: datetime | None = None
    stability_restart_count: int | None = Field(default=None, ge=0)


class RecoveryVerifier:
    """Checks readiness and required service indicators outside the agent loop."""

    def __init__(
        self,
        workloads: WorkloadHealthReader,
        indicators: ServiceIndicatorReader | None = None,
        max_5xx_rate: float = 0.01,
        restart_stability_window_seconds: int = 30,
    ) -> None:
        self._workloads = workloads
        self._indicators = indicators
        self._max_5xx_rate = max_5xx_rate
        self._restart_stability_window_seconds = restart_stability_window_seconds

    def verify(self, plan: ActionPlan, now: datetime | None = None) -> RecoveryResult:
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
        if plan.proposal.action_type is ActionType.RESTORE_RESPONSE_MODE:
            if self._indicators is None:
                return RecoveryResult(
                    recovered=False,
                    reason="response-mode recovery requires a configured 5xx service indicator",
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

        if plan.proposal.action_type in {ActionType.RESTORE_MEMORY_MODE, ActionType.RESTART}:
            return self._restart_stability(plan, workload, now or datetime.now(UTC))

        return RecoveryResult(
            recovered=True,
            reason="workload has reached its desired ready replica count",
            workload=workload,
        )

    def _restart_stability(
        self, plan: ActionPlan, workload: WorkloadStatus, now: datetime
    ) -> RecoveryResult:
        """Require a stable restart count over time for memory/restart recovery."""

        started_at = plan.stability_observed_at
        baseline = plan.stability_restart_count
        if started_at is None or baseline is None or workload.restart_count != baseline:
            reason = (
                "started a new 30-second restart-stability observation"
                if started_at is None or baseline is None
                else "restart count changed; restarted the 30-second stability observation"
            )
            return RecoveryResult(
                recovered=False,
                pending=True,
                reason=reason,
                workload=workload,
                stability_window_remaining_seconds=self._restart_stability_window_seconds,
                stability_observed_at=now,
                stability_restart_count=workload.restart_count,
            )
        elapsed = int((now - started_at).total_seconds())
        remaining = max(0, self._restart_stability_window_seconds - elapsed)
        if remaining:
            return RecoveryResult(
                recovered=False,
                pending=True,
                reason=f"restart count is stable; {remaining}s remain in the observation window",
                workload=workload,
                stability_window_remaining_seconds=remaining,
                stability_observed_at=started_at,
                stability_restart_count=baseline,
            )
        return RecoveryResult(
            recovered=True,
            reason="workload is ready and restart count remained stable for 30 seconds",
            workload=workload,
            stability_window_remaining_seconds=0,
            stability_observed_at=started_at,
            stability_restart_count=baseline,
        )
