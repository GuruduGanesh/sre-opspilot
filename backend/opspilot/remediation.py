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
        response = self._apps_api.patch_namespaced_deployment(
            proposal.workload,
            proposal.namespace,
            self._patch(proposal),
            dry_run="All",
        )
        return {
            "dry_run": True,
            "action_type": proposal.action_type.value,
            "target": f"{proposal.namespace}/deployments/{proposal.workload}",
            "resource_version": response.metadata.resource_version,
            "patch": self._patch(proposal),
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
        if proposal.action_type is ActionType.ROLLBACK:
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
            return {
                "metadata": metadata,
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "opspilot.dev/restarted-at": datetime.now(UTC).isoformat()
                            }
                        }
                    }
                },
            }
        assert proposal.target_replicas is not None
        return {"metadata": metadata, "spec": {"replicas": proposal.target_replicas}}

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
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._namespace = namespace
        self._workload = workload
        self._policy = policy or ActionPolicy(namespace=namespace, workloads={workload})
        self._recovery_max_5xx_rate = recovery_max_5xx_rate

    def propose(
        self,
        incident_id: str,
        action_type: ActionType,
        evidence_ids: list[str],
        target_replicas: int | None = None,
    ) -> ActionPlan:
        incident = self._store.incident(incident_id)
        if incident is None:
            raise KeyError(f"incident not found: {incident_id}")
        if incident["lifecycle_state"] != LifecycleState.TRIAGING.value:
            raise ValueError("action proposals require an incident in Triaging")
        known_evidence = {item.id for item in self._store.list_evidence(incident_id)}
        if not evidence_ids or set(evidence_ids) - known_evidence:
            raise ValueError("action proposal must cite evidence from the current incident")
        proposal = ActionProposal(
            incident_id=incident_id,
            action_type=action_type,
            namespace=self._namespace,
            workload=self._workload,
            evidence_ids=evidence_ids,
            expected_resource_version=self._adapter.resource_version(
                self._namespace, self._workload
            ),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            target_replicas=target_replicas,
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

    def execute(self, action_id: str) -> ActionPlan:
        plan = self._require(action_id)
        self._validate_fingerprint(plan)
        current_version = self._adapter.resource_version(
            plan.proposal.namespace, plan.proposal.workload
        )
        self._policy.validate_for_execution(
            plan.proposal, plan.approved_at, current_version
        )
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

    def verify(
        self, action_id: str, verifier: RecoveryVerifier
    ) -> tuple[ActionPlan, RecoveryResult]:
        plan = self._require(action_id)
        if plan.status is not ActionPlanStatus.EXECUTED:
            raise ValueError("only an executed action plan can be verified")
        # A temporary verifier outage is not recovery evidence and must leave the
        # approved, executed plan retryable instead of trapping it in Verifying.
        result = verifier.verify(plan)
        if result.recovered:
            completed = plan.model_copy(update={"status": ActionPlanStatus.VERIFIED})
            target = LifecycleState.RESOLVED
            reason = f"independent recovery verified for action plan {plan.id}: {result.reason}"
        else:
            completed = plan.model_copy(update={"status": ActionPlanStatus.FAILED})
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
        if proposal.action_type is ActionType.ROLLBACK:
            checks.append(
                {
                    "kind": "metric_threshold",
                    "query": "service_5xx_recovery_rate",
                    "window": "15 seconds",
                    "maximum": self._recovery_max_5xx_rate,
                }
            )
        return {"independent": True, "checks": checks}
