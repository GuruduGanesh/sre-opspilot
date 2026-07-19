"""Server-owned remediation planning and execution for the controlled cluster."""

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from kubernetes import client, config

from opspilot.domain.actions import (
    ActionPlan,
    ActionPlanStatus,
    ActionPolicy,
    ActionProposal,
    ActionType,
    action_fingerprint,
)
from opspilot.domain.incidents import LifecycleState
from opspilot.recovery import RecoveryResult, RecoveryVerifier
from opspilot.storage.incidents import SQLiteIncidentStore


class DeploymentRemediationAdapter(Protocol):
    def resource_version(self, namespace: str, workload: str) -> str: ...

    def preview(self, proposal: ActionProposal) -> dict[str, object]: ...

    def execute(self, proposal: ActionProposal) -> dict[str, object]: ...


class KubernetesRemediationAdapter:
    """Narrow Kubernetes write adapter limited to the demo allowlist."""

    def __init__(
        self,
        apps_api: client.AppsV1Api | None = None,
        allowed_namespace: str = "opspilot-demo",
        allowed_workloads: set[str] | None = None,
    ) -> None:
        if apps_api is None:
            config.load_kube_config()
            apps_api = client.AppsV1Api()
        self._apps_api = apps_api
        self._allowed_namespace = allowed_namespace
        self._allowed_workloads = allowed_workloads or {"checkout"}

    def resource_version(self, namespace: str, workload: str) -> str:
        self._validate_target(namespace, workload)
        deployment = self._apps_api.read_namespaced_deployment(workload, namespace)
        return deployment.metadata.resource_version

    def preview(self, proposal: ActionProposal) -> dict[str, object]:
        self._validate_target(proposal.namespace, proposal.workload)
        deployment = self._apps_api.read_namespaced_deployment(
            proposal.workload, proposal.namespace
        )
        if deployment.metadata.resource_version != proposal.expected_resource_version:
            raise ValueError("action proposal is stale because the target changed")
        changes = self._planned_changes(deployment, proposal)
        if all(change["before"] == change["after"] for change in changes):
            raise ValueError(
                "action preview would make no controlled change; continue triage or choose "
                "a different allowlisted action"
            )
        patch = self._patch(proposal)
        response = self._apps_api.patch_namespaced_deployment(
            proposal.workload,
            proposal.namespace,
            patch,
            dry_run="All",
        )
        return {
            "dry_run": True,
            "action_type": proposal.action_type.value,
            "target": f"{proposal.namespace}/deployments/{proposal.workload}",
            "resource_version": response.metadata.resource_version,
            "patch": patch,
            "changes": changes,
        }

    def execute(self, proposal: ActionProposal) -> dict[str, object]:
        self._validate_target(proposal.namespace, proposal.workload)
        response = self._apps_api.patch_namespaced_deployment(
            proposal.workload, proposal.namespace, self._patch(proposal)
        )
        return {
            "action_type": proposal.action_type.value,
            "target": f"{proposal.namespace}/deployments/{proposal.workload}",
            "resource_version": response.metadata.resource_version,
        }

    @staticmethod
    def _patch(proposal: ActionProposal) -> dict[str, object]:
        metadata = {"resourceVersion": proposal.expected_resource_version}
        if proposal.action_type is ActionType.RESTORE_RESPONSE_MODE:
            return {
                "metadata": metadata,
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": proposal.workload,
                                    "env": [{"name": "FAIL_MODE", "value": "false"}],
                                }
                            ]
                        }
                    }
                },
            }
        if proposal.action_type is ActionType.RESTORE_MEMORY_MODE:
            return {
                "metadata": metadata,
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": proposal.workload,
                                    "env": [{"name": "MEMORY_LEAK_MODE", "value": "false"}],
                                }
                            ]
                        }
                    }
                },
            }
        if proposal.action_type is ActionType.RESTART:
            if proposal.restart_at is None:
                raise ValueError("restart requires a server-generated restart timestamp")
            return {
                "metadata": metadata,
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "opspilot.dev/restarted-at": proposal.restart_at.isoformat()
                            }
                        }
                    }
                },
            }
        if proposal.target_replicas is None:
            raise ValueError("scale requires a bounded target replica count")
        return {"metadata": metadata, "spec": {"replicas": proposal.target_replicas}}

    @staticmethod
    def _planned_changes(
        deployment: client.V1Deployment, proposal: ActionProposal
    ) -> list[dict[str, str]]:
        """Return a compact before/after summary from the deployment read used by preview."""

        def env_value(name: str) -> str:
            containers = deployment.spec.template.spec.containers
            container = next(
                (item for item in containers if item.name == proposal.workload), None
            )
            if container is None:
                return "<container not found>"
            for item in container.env or []:
                if item.name == name:
                    return item.value or "<valueFrom>"
            return "<unset>"

        if proposal.action_type is ActionType.RESTORE_RESPONSE_MODE:
            return [
                {
                    "field": "FAIL_MODE",
                    "before": env_value("FAIL_MODE"),
                    "after": "false",
                    "effect": "A rollout replaces the checkout pod.",
                }
            ]
        if proposal.action_type is ActionType.RESTORE_MEMORY_MODE:
            return [
                {
                    "field": "MEMORY_LEAK_MODE",
                    "before": env_value("MEMORY_LEAK_MODE"),
                    "after": "false",
                    "effect": "A rollout replaces the checkout pod.",
                }
            ]
        if proposal.action_type is ActionType.RESTART:
            annotations = deployment.spec.template.metadata.annotations or {}
            return [
                {
                    "field": "opspilot.dev/restarted-at",
                    "before": annotations.get("opspilot.dev/restarted-at", "<unset>"),
                    "after": proposal.restart_at.isoformat()
                    if proposal.restart_at
                    else "<unset>",
                    "effect": "A rollout restarts the checkout pod.",
                }
            ]
        return [
            {
                "field": "spec.replicas",
                "before": str(deployment.spec.replicas),
                "after": str(proposal.target_replicas),
                "effect": "Kubernetes adjusts the checkout replica count.",
            }
        ]

    def _validate_target(self, namespace: str, workload: str) -> None:
        if namespace != self._allowed_namespace or workload not in self._allowed_workloads:
            raise ValueError("remediation target is outside the controlled allowlist")


class RemediationCoordinator:
    def __init__(
        self,
        store: SQLiteIncidentStore,
        adapter: DeploymentRemediationAdapter,
        policy: ActionPolicy | None = None,
        namespace: str = "opspilot-demo",
        workload: str = "checkout",
        recovery_max_5xx_rate: float = 0.01,
        recovery_min_2xx_rate: float = 0.01,
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._namespace = namespace
        self._workload = workload
        self._policy = policy or ActionPolicy(namespace=namespace, workloads={workload})
        self._recovery_max_5xx_rate = recovery_max_5xx_rate
        self._recovery_min_2xx_rate = recovery_min_2xx_rate

    def propose(
        self,
        incident_id: str,
        action_type: ActionType,
        evidence_ids: list[str],
        target_replicas: int | None = None,
        requested_by: str = "local-oncall",
    ) -> ActionPlan:
        incident = self._store.incident(incident_id)
        if incident is None:
            raise KeyError(f"incident not found: {incident_id}")
        if incident["lifecycle_state"] != LifecycleState.TRIAGING.value:
            raise ValueError("action proposals require an incident in Triaging")
        known_evidence = {item.id for item in self._store.list_evidence(incident_id)}
        if not evidence_ids or set(evidence_ids) - known_evidence:
            raise ValueError("action proposal must cite evidence from the current incident")
        proposed_at = datetime.now(UTC)
        proposal = ActionProposal(
            incident_id=incident_id,
            action_type=action_type,
            namespace=self._namespace,
            workload=self._workload,
            evidence_ids=evidence_ids,
            expected_resource_version=self._adapter.resource_version(
                self._namespace, self._workload
            ),
            expires_at=proposed_at + timedelta(minutes=5),
            requested_by=requested_by,
            proposed_at=proposed_at,
            target_replicas=target_replicas,
            restart_at=proposed_at if action_type is ActionType.RESTART else None,
        )
        self._policy.validate_for_preview(proposal)
        preview = self._adapter.preview(proposal)
        preview["verification_plan"] = self._verification_plan(proposal)
        plan = ActionPlan(
            id=str(uuid4()),
            proposal=proposal,
            preview=preview,
            fingerprint=action_fingerprint(proposal, preview),
            status=ActionPlanStatus.PREVIEWED,
        )
        self._store.create_action_plan(plan)
        self._store.transition(
            incident_id,
            LifecycleState.ACTION_PROPOSED,
            actor="opspilot-server",
            reason=f"created {action_type.value} preview {plan.id}",
        )
        return plan

    def approve(self, action_id: str, approved_by: str) -> ActionPlan:
        plan = self._require(action_id)
        self._validate_fingerprint(plan)
        self._expire_if_stale(plan)
        plan = self._require(action_id)
        if plan.status is not ActionPlanStatus.PREVIEWED:
            raise ValueError("only a previewed action plan can be approved")
        approved = plan.model_copy(
            update={
                "status": ActionPlanStatus.APPROVED,
                "approved_at": datetime.now(UTC),
                "approved_by": approved_by,
            }
        )
        self._store.update_action_plan(approved, expected_status=ActionPlanStatus.PREVIEWED)
        return approved

    def reject(self, action_id: str, rejected_by: str, reason: str | None = None) -> ActionPlan:
        """Record an explicit human rejection and return the incident to triage."""

        plan = self._require(action_id)
        self._validate_fingerprint(plan)
        self._expire_if_stale(plan)
        plan = self._require(action_id)
        if plan.status is not ActionPlanStatus.PREVIEWED:
            raise ValueError("only a previewed action plan can be rejected")
        rejected = plan.model_copy(
            update={
                "status": ActionPlanStatus.REJECTED,
                "rejected_at": datetime.now(UTC),
                "rejected_by": rejected_by,
                "rejection_reason": reason,
            }
        )
        self._store.update_action_plan(rejected, expected_status=ActionPlanStatus.PREVIEWED)
        self._store.transition(
            plan.proposal.incident_id,
            LifecycleState.TRIAGING,
            actor=rejected_by,
            reason=f"rejected action plan {plan.id}" + (f": {reason}" if reason else ""),
        )
        return rejected

    def execute(self, action_id: str) -> ActionPlan:
        plan = self._require(action_id)
        self._validate_fingerprint(plan)
        current_version = self._adapter.resource_version(
            plan.proposal.namespace, plan.proposal.workload
        )
        try:
            self._policy.validate_for_execution(
                plan.proposal, plan.approved_at, current_version
            )
        except ValueError as error:
            if plan.status is ActionPlanStatus.APPROVED and "stale" in str(error):
                self._invalidate_approved_plan(plan, str(error))
            raise
        if plan.status is not ActionPlanStatus.APPROVED:
            raise ValueError("only an approved action plan can execute")
        incident = self._store.incident(plan.proposal.incident_id)
        if incident is None:
            raise KeyError(f"incident not found: {plan.proposal.incident_id}")
        if incident["lifecycle_state"] != LifecycleState.ACTION_PROPOSED.value:
            raise ValueError("approved action is no longer attached to an actionable incident")
        self._store.transition(
            plan.proposal.incident_id,
            LifecycleState.EXECUTING,
            actor="opspilot-server",
            reason=f"executing approved action plan {plan.id}",
        )
        executing = plan.model_copy(update={"status": ActionPlanStatus.EXECUTING})
        self._store.update_action_plan(executing, expected_status=ActionPlanStatus.APPROVED)
        try:
            self._adapter.execute(plan.proposal)
        except Exception:
            self._store.transition(
                plan.proposal.incident_id,
                LifecycleState.TRIAGING,
                actor="opspilot-server",
                reason=f"execution failed for action plan {plan.id}",
            )
            failed = executing.model_copy(update={"status": ActionPlanStatus.FAILED})
            self._store.update_action_plan(failed, expected_status=ActionPlanStatus.EXECUTING)
            raise
        executed = executing.model_copy(
            update={"status": ActionPlanStatus.EXECUTED, "executed_at": datetime.now(UTC)}
        )
        self._store.update_action_plan(executed, expected_status=ActionPlanStatus.EXECUTING)
        self._store.transition(
            plan.proposal.incident_id,
            LifecycleState.MONITORING,
            actor="opspilot-server",
            reason=f"awaiting independent recovery verification for action plan {plan.id}",
        )
        return executed

    def _invalidate_approved_plan(self, plan: ActionPlan, reason: str) -> None:
        """Audit a plan that was safe at preview time but changed before execution."""

        stale = plan.model_copy(
            update={
                "status": ActionPlanStatus.STALE,
                "invalidated_at": datetime.now(UTC),
                "invalidation_reason": reason,
            }
        )
        self._store.update_action_plan(stale, expected_status=ActionPlanStatus.APPROVED)
        incident = self._store.incident(plan.proposal.incident_id)
        if incident and incident["lifecycle_state"] == LifecycleState.ACTION_PROPOSED.value:
            self._store.transition(
                plan.proposal.incident_id,
                LifecycleState.TRIAGING,
                actor="opspilot-server",
                reason=f"approved action plan {plan.id} invalidated before execution: {reason}",
            )

    def verify(
        self, action_id: str, verifier: RecoveryVerifier, now: datetime | None = None
    ) -> tuple[ActionPlan, RecoveryResult]:
        plan = self._require(action_id)
        if plan.status is not ActionPlanStatus.EXECUTED:
            raise ValueError("only an executed action plan can be verified")
        # A temporary verifier outage is not recovery evidence and must leave the
        # approved, executed plan retryable instead of trapping it in Verifying.
        result = verifier.verify(plan, now=now)
        if result.pending:
            pending = plan.model_copy(
                update={
                    "stability_observed_at": result.stability_observed_at,
                    "stability_restart_count": result.stability_restart_count,
                    "recovery": result.model_dump(mode="json"),
                }
            )
            self._store.update_action_plan(pending, expected_status=ActionPlanStatus.EXECUTED)
            return pending, result
        if result.recovered:
            completed = plan.model_copy(
                update={
                    "status": ActionPlanStatus.VERIFIED,
                    "recovery": result.model_dump(mode="json"),
                }
            )
            target = LifecycleState.RESOLVED
            reason = f"independent recovery verified for action plan {plan.id}: {result.reason}"
        else:
            completed = plan.model_copy(
                update={
                    "status": ActionPlanStatus.FAILED,
                    "recovery": result.model_dump(mode="json"),
                }
            )
            target = LifecycleState.TRIAGING
            reason = f"recovery verification failed for action plan {plan.id}: {result.reason}"
        self._store.update_action_plan(completed)
        self._store.transition(
            plan.proposal.incident_id,
            target,
            actor="opspilot-verifier",
            reason=reason,
        )
        return completed, result

    def _require(self, action_id: str) -> ActionPlan:
        plan = self._store.action_plan(action_id)
        if plan is None:
            raise KeyError(f"action plan not found: {action_id}")
        return plan

    def list_for_incident(self, incident_id: str) -> list[ActionPlan]:
        """Return plans after reconciling stale previews with their incident state."""

        plans = self._store.list_action_plans(incident_id)
        for plan in plans:
            self._expire_if_stale(plan)
        return self._store.list_action_plans(incident_id)

    def _expire_if_stale(self, plan: ActionPlan, now: datetime | None = None) -> bool:
        """Expire a preview once, then make the incident actionable again.

        This lazy reconciliation keeps both the plan audit trail and the visible
        lifecycle truthful without relying on a background worker in the local demo.
        """

        if plan.status is not ActionPlanStatus.PREVIEWED:
            return False
        if plan.proposal.expires_at > (now or datetime.now(UTC)):
            return False
        expired = plan.model_copy(update={"status": ActionPlanStatus.EXPIRED})
        self._store.update_action_plan(expired, expected_status=ActionPlanStatus.PREVIEWED)
        incident = self._store.incident(plan.proposal.incident_id)
        if incident and incident["lifecycle_state"] == LifecycleState.ACTION_PROPOSED.value:
            self._store.transition(
                plan.proposal.incident_id,
                LifecycleState.TRIAGING,
                actor="opspilot-server",
                reason=f"action plan {plan.id} expired; create a fresh preview",
            )
        return True

    @staticmethod
    def _validate_fingerprint(plan: ActionPlan) -> None:
        if action_fingerprint(plan.proposal, plan.preview) != plan.fingerprint:
            raise ValueError("action plan preview changed; create a new preview before approval")

    def _verification_plan(self, proposal: ActionProposal) -> dict[str, object]:
        """Expose the deterministic recovery contract before an engineer approves."""

        checks: list[dict[str, object]] = [
            {
                "kind": "workload_readiness",
                "target": f"{proposal.namespace}/deployments/{proposal.workload}",
                "condition": "ready replicas must equal desired replicas",
            }
        ]
        if proposal.action_type is ActionType.RESTORE_RESPONSE_MODE:
            checks.append(
                {
                    "kind": "metric_threshold",
                    "query": "service_5xx_recovery_rate",
                    "window": "15 seconds",
                    "maximum": self._recovery_max_5xx_rate,
                }
            )
            checks.append(
                {
                    "kind": "metric_threshold",
                    "query": "service_2xx_recovery_rate",
                    "window": "15 seconds",
                    "minimum": self._recovery_min_2xx_rate,
                }
            )
        if proposal.action_type in {ActionType.RESTORE_MEMORY_MODE, ActionType.RESTART}:
            checks.append(
                {
                    "kind": "restart_stability",
                    "condition": "restart count must not increase for 30 seconds",
                    "window": "30 seconds",
                }
            )
        return {"independent": True, "checks": checks}
