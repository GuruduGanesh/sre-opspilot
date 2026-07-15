import re
from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config

from opspilot.domain.tools import DeploymentRevision, KubernetesEvent, LogExcerpt, WorkloadStatus

_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|secret|token|api[_-]?key)\b\s*([=:])\s*[^\s,;]+"
)


class KubernetesAdapter:
    """Read-only Kubernetes adapter scoped by the caller's allowed namespace."""

    def __init__(
        self,
        apps_api: client.AppsV1Api | None = None,
        core_api: client.CoreV1Api | None = None,
    ) -> None:
        if apps_api is None or core_api is None:
            config.load_kube_config()
            apps_api = apps_api or client.AppsV1Api()
            core_api = core_api or client.CoreV1Api()
        self._apps_api = apps_api
        self._core_api = core_api

    def get_workload_status(self, namespace: str, workload: str) -> WorkloadStatus:
        deployment = self._apps_api.read_namespaced_deployment(workload, namespace)
        selector = _label_selector(deployment.spec.selector.match_labels or {})
        pods = self._core_api.list_namespaced_pod(namespace, label_selector=selector).items
        restart_count = sum(_pod_restart_count(pod) for pod in pods)
        return WorkloadStatus(
            namespace=namespace,
            workload=workload,
            ready_replicas=deployment.status.ready_replicas or 0,
            desired_replicas=deployment.spec.replicas or 0,
            restart_count=restart_count,
            observed_at=datetime.now(UTC),
        )

    def get_events(self, namespace: str, workload: str, limit: int = 20) -> list[KubernetesEvent]:
        if limit < 1 or limit > 100:
            raise ValueError("event limit must be between 1 and 100")
        events = self._core_api.list_namespaced_event(namespace).items
        matching = [
            event
            for event in events
            if getattr(getattr(event, "involved_object", None), "name", None) == workload
            or getattr(getattr(event, "involved_object", None), "kind", "") == "Pod"
            and workload in getattr(getattr(event, "involved_object", None), "name", "")
        ]
        return [self._event_record(namespace, event) for event in matching[-limit:]]

    def get_log_excerpt(
        self, namespace: str, pod: str, container: str, tail_lines: int = 100
    ) -> LogExcerpt:
        if tail_lines < 1 or tail_lines > 500:
            raise ValueError("log tail must be between 1 and 500 lines")
        raw = self._core_api.read_namespaced_pod_log(
            pod, namespace, container=container, tail_lines=tail_lines, timestamps=True
        )
        lines = raw.splitlines()
        redacted = [_SECRET_PATTERN.sub(r"\1\2[REDACTED]", line) for line in lines]
        return LogExcerpt(
            namespace=namespace,
            pod=pod,
            container=container,
            lines=redacted,
            redacted_line_count=sum(
                before != after for before, after in zip(lines, redacted, strict=True)
            ),
            observed_at=datetime.now(UTC),
            source_ref=f"k8s://{namespace}/pods/{pod}/log?container={container}&tail={tail_lines}",
        )

    def get_deployment_history(
        self, namespace: str, deployment: str
    ) -> list[DeploymentRevision]:
        deployment_object = self._apps_api.read_namespaced_deployment(deployment, namespace)
        selector = _label_selector(deployment_object.spec.selector.match_labels or {})
        replica_sets = self._apps_api.list_namespaced_replica_set(
            namespace, label_selector=selector
        ).items
        revisions: list[DeploymentRevision] = []
        for replica_set in replica_sets:
            owners = getattr(replica_set.metadata, "owner_references", None) or []
            if not any(owner.kind == "Deployment" and owner.name == deployment for owner in owners):
                continue
            annotations = replica_set.metadata.annotations or {}
            revision = annotations.get("deployment.kubernetes.io/revision")
            if revision is None:
                continue
            containers = replica_set.spec.template.spec.containers or []
            revisions.append(
                DeploymentRevision(
                    namespace=namespace,
                    deployment=deployment,
                    revision=revision,
                    images=[container.image for container in containers],
                    observed_at=_as_utc(replica_set.metadata.creation_timestamp),
                    source_ref=f"k8s://{namespace}/replicasets/{replica_set.metadata.name}",
                )
            )
        return sorted(revisions, key=lambda item: int(item.revision))

    @staticmethod
    def _event_record(namespace: str, event: Any) -> KubernetesEvent:
        involved = event.involved_object
        observed = (
            getattr(event, "event_time", None)
            or getattr(event, "last_timestamp", None)
            or getattr(event.metadata, "creation_timestamp", None)
        )
        return KubernetesEvent(
            namespace=namespace,
            event_type=getattr(event, "type", None) or "Normal",
            reason=getattr(event, "reason", None) or "Unknown",
            message=getattr(event, "message", None) or "",
            involved_object=(
                f"{getattr(involved, 'kind', 'Unknown')}/"
                f"{getattr(involved, 'name', 'unknown')}"
            ),
            observed_at=_as_utc(observed),
            source_ref=f"k8s://{namespace}/events/{event.metadata.name}",
        )


class FakeKubernetesAdapter:
    """Deterministic adapter used by contract tests before cluster integration."""

    def get_workload_status(self, namespace: str, workload: str) -> WorkloadStatus:
        return WorkloadStatus(
            namespace=namespace,
            workload=workload,
            ready_replicas=1,
            desired_replicas=1,
            restart_count=0,
            observed_at=datetime.now(UTC),
        )

    def get_events(self, namespace: str, workload: str, limit: int = 20) -> list[KubernetesEvent]:
        return []

    def get_log_excerpt(
        self, namespace: str, pod: str, container: str, tail_lines: int = 100
    ) -> LogExcerpt:
        return LogExcerpt(
            namespace=namespace,
            pod=pod,
            container=container,
            lines=[],
            redacted_line_count=0,
            observed_at=datetime.now(UTC),
            source_ref=f"fake://{namespace}/pods/{pod}/log",
        )

    def get_deployment_history(
        self, namespace: str, deployment: str
    ) -> list[DeploymentRevision]:
        return []


def _label_selector(match_labels: dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in sorted(match_labels.items()))


def _pod_restart_count(pod: Any) -> int:
    statuses = pod.status.container_statuses or []
    return sum(status.restart_count or 0 for status in statuses)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
