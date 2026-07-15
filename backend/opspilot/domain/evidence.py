from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceSourceType(StrEnum):
    ALERT = "alert"
    KUBERNETES = "kubernetes"
    PROMETHEUS = "prometheus"
    LOG = "log"
    DEPLOYMENT = "deployment"


class EvidenceRecord(BaseModel):
    """Normalized evidence that can later be cited by a hypothesis."""

    model_config = ConfigDict(extra="forbid")

    id: str
    incident_id: str
    source_type: EvidenceSourceType
    source_ref: str
    observed_at: datetime
    summary: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
