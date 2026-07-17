from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WorkloadStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    workload: str
    ready_replicas: int = Field(ge=0)
    desired_replicas: int = Field(ge=0)
    restart_count: int = Field(ge=0)
    observed_at: datetime


class MetricQueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_name: str
    value: float
    observed_at: datetime
    source_ref: str


class KubernetesEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    event_type: str
    reason: str
    message: str
    involved_object: str
    observed_at: datetime
    source_ref: str


class LogExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    pod: str
    container: str
    lines: list[str]
    redacted_line_count: int = Field(ge=0)
    observed_at: datetime
    source_ref: str


class DeploymentRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str
    deployment: str
    revision: str
    images: list[str]
    controlled_config: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime
    source_ref: str


class KubernetesEvidenceAdapter(Protocol):
    def get_workload_status(self, namespace: str, workload: str) -> WorkloadStatus: ...

    def get_events(
        self, namespace: str, workload: str, limit: int = 20
    ) -> list[KubernetesEvent]: ...

    def get_log_excerpt(
        self, namespace: str, pod: str, container: str, tail_lines: int = 100
    ) -> LogExcerpt: ...

    def get_deployment_history(
        self, namespace: str, deployment: str
    ) -> list[DeploymentRevision]: ...


class PrometheusEvidenceAdapter(Protocol):
    def get_metric(self, query_name: str, service: str) -> MetricQueryResult: ...
