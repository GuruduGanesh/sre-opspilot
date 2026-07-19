"""Independent, deterministic recovery checks for controlled remediations."""

from datetime import UTC, datetime
from typing import Protocol

import httpx
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
    service_2xx_rate: float | None = Field(default=None, ge=0)
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
        min_2xx_rate: float = 0.01,
        restart_stability_window_seconds: int = 30,
        response_recovery_window_seconds: int = 15,
    ) -> None:
        self._workloads = workloads
        self._indicators = indicators
        self._max_5xx_rate = max_5xx_rate
        self._min_2xx_rate = min_2xx_rate
        self._restart_stability_window_seconds = restart_stability_window_seconds
        self._response_recovery_window_seconds = response_recovery_window_seconds

    def verify(self, plan: ActionPlan, now: datetime | None = None) -> RecoveryResult:
        workload = self._workloads.get_workload_status(
            plan.proposal.namespace, plan.proposal.workload
        )
        if workload.ready_replicas < workload.desired_replicas or workload.desired_replicas < 1:
            return RecoveryResult(
                recovered=False,
                pending=True,
                reason=(
                    "workload has not reached its desired ready replica count; "
                    "waiting for the controlled rollout to become ready"
                ),
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
            try:
                metric = self._indicators.get_metric("service_5xx_recovery_rate", "checkout")
                success_metric = self._indicators.get_metric(
                    "service_2xx_recovery_rate", "checkout"
                )
            except httpx.HTTPError:
                # A dropped local port-forward is not evidence of recovery or
                # failure. Keep the approved action in Monitoring so the
                # engineer can retry the independent check without reapplying.
                return RecoveryResult(
                    recovered=False,
                    pending=True,
                    reason=(
                        "controlled Prometheus telemetry is temporarily unavailable; "
                        "recovery remains unverified"
                    ),
                    workload=workload,
                )
            if metric.value > self._max_5xx_rate:
                return self._response_observation(
                    plan,
                    workload,
                    now or datetime.now(UTC),
                    metric.value,
                    success_metric.value,
                    waiting_reason="checkout 5xx rate is still above the recovery threshold",
                    failed_reason=(
                        "checkout 5xx rate remains above the recovery threshold "
                        f"({metric.value:.3f} > {self._max_5xx_rate:.3f})"
                    ),
                )
            if success_metric.value < self._min_2xx_rate:
                return self._response_observation(
                    plan,
                    workload,
                    now or datetime.now(UTC),
                    metric.value,
                    success_metric.value,
                    waiting_reason="checkout 2xx rate is below the recovery traffic threshold",
                    failed_reason=(
                        "checkout 2xx rate remains below the recovery traffic threshold "
                        f"({success_metric.value:.3f} < {self._min_2xx_rate:.3f})"
                    ),
                    keep_monitoring=True,
                )
            return RecoveryResult(
                recovered=True,
                reason=(
                    "workload is ready, checkout 5xx rate is within the recovery threshold, "
                    "and checkout 2xx traffic is flowing"
                ),
                workload=workload,
                service_5xx_rate=metric.value,
                service_2xx_rate=success_metric.value,
            )

        if plan.proposal.action_type in {ActionType.RESTORE_MEMORY_MODE, ActionType.RESTART}:
            return self._restart_stability(plan, workload, now or datetime.now(UTC))

        return RecoveryResult(
            recovered=True,
            reason="workload has reached its desired ready replica count",
            workload=workload,
        )

    def _response_observation(
        self,
        plan: ActionPlan,
        workload: WorkloadStatus,
        observed_at: datetime,
        service_5xx_rate: float,
        service_2xx_rate: float,
        *,
        waiting_reason: str,
        failed_reason: str,
        keep_monitoring: bool = False,
    ) -> RecoveryResult:
        """Give the 15-second post-action traffic window time to settle."""

        started_at = plan.stability_observed_at
        if started_at is None:
            return RecoveryResult(
                recovered=False,
                pending=True,
                reason=(
                    f"{waiting_reason}; waiting {self._response_recovery_window_seconds}s "
                    "for the post-action Prometheus rate window"
                ),
                workload=workload,
                service_5xx_rate=service_5xx_rate,
                service_2xx_rate=service_2xx_rate,
                stability_window_remaining_seconds=self._response_recovery_window_seconds,
                stability_observed_at=observed_at,
            )
        elapsed = int((observed_at - started_at).total_seconds())
        remaining = max(0, self._response_recovery_window_seconds - elapsed)
        if remaining:
            return RecoveryResult(
                recovered=False,
                pending=True,
                reason=f"{waiting_reason}; {remaining}s remain for the post-action rate window",
                workload=workload,
                service_5xx_rate=service_5xx_rate,
                service_2xx_rate=service_2xx_rate,
                stability_window_remaining_seconds=remaining,
                stability_observed_at=started_at,
            )
        if keep_monitoring:
            return RecoveryResult(
                recovered=False,
                pending=True,
                reason=(
                    f"{waiting_reason}; the rate window is clear but no successful checkout "
                    "traffic has been observed yet"
                ),
                workload=workload,
                service_5xx_rate=service_5xx_rate,
                service_2xx_rate=service_2xx_rate,
                stability_window_remaining_seconds=0,
                stability_observed_at=started_at,
            )
        return RecoveryResult(
            recovered=False,
            reason=failed_reason,
            workload=workload,
            service_5xx_rate=service_5xx_rate,
            service_2xx_rate=service_2xx_rate,
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
